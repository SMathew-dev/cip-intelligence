from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from .models import ContextPolicy, ProductionRun

ENGINE_VERSION = "0.1.0"

# These floors are only numerical guardrails for robust similarity/anomaly calculations.
# They are not food-process limits and never imply cleaning adequacy.
CONTEXT_SCALE_FLOORS = {
    "h": 0.25,
    "L": 500.0,
    "%": 0.25,
    "C": 0.5,
    "min": 2.0,
    "bar": 0.03,
    "count": 1.0,
    "%decline": 1.0,
}
BEHAVIOR_SCALE_FLOORS = {
    "s": 5.0,
    "C": 0.20,
    "L/min": 1.5,
    "mS/cm": 0.08,
    "bar": 0.015,
}


def _parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        ts = value
    else:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ValueError("L4 requires timezone-aware timestamps")
    return ts


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    w = pos - lo
    return ordered[lo] * (1 - w) + ordered[hi] * w


def _distribution(values: list[float], unit: str | None, *, floors: dict[str, float]) -> dict[str, Any]:
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    q1 = _quantile(values, 0.25)
    q3 = _quantile(values, 0.75)
    robust_sigma = 1.4826 * mad
    iqr_sigma = (q3 - q1) / 1.349 if q3 > q1 else 0.0
    scale_floor = floors.get(unit or "", max(abs(med) * 0.0025, 1e-9))
    scale = max(robust_sigma, iqr_sigma, scale_floor)
    return {
        "n": len(values), "median": med, "q1": q1, "q3": q3, "mad": mad,
        "robust_scale": scale, "minimum": min(values), "maximum": max(values), "unit": unit,
    }


def _value(item: dict | None) -> float | None:
    if not item:
        return None
    val = item.get("value")
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _weighted_average(items: list[tuple[float, float]]) -> float | None:
    usable = [(v, w) for v, w in items if math.isfinite(v) and math.isfinite(w) and w > 0]
    if not usable:
        return None
    total_w = sum(w for _, w in usable)
    return sum(v * w for v, w in usable) / total_w if total_w > 0 else None


def _metric(run: ProductionRun, name: str) -> float | None:
    return getattr(run.metrics, name)


def build_production_context(cycle: dict, runs: list[ProductionRun | dict], *, policy: ContextPolicy | None = None,
                             previous_cip_end: str | datetime | None = None) -> dict:
    """Link a CIP to the contiguous production campaign immediately preceding it.

    L4 never calls this a measured soil load. It reconstructs production context and
    exposes fouling-associated process indicators only when the plant supplied them.
    """
    policy = policy or ContextPolicy()
    start = _parse_ts(cycle["start_ts"])
    asset = cycle["asset"]
    parsed = [r if isinstance(r, ProductionRun) else ProductionRun.model_validate(r) for r in runs]
    same_asset = sorted([r for r in parsed if r.asset == asset], key=lambda r: r.start_ts)

    overlaps = [r for r in same_asset if r.start_ts < start < r.end_ts or r.start_ts == start]
    if overlaps:
        return {
            "cycle_id": cycle["cycle_id"], "asset": asset, "context_status": "CONFLICT",
            "campaign": None,
            "issues": [{"code": "PRODUCTION_OVERLAPS_CIP", "severity": "HIGH", "run_ids": [r.run_id for r in overlaps]}],
            "context_features": {}, "fouling_associated_indicators": {},
            "principle": "Production context is descriptive evidence; it is not direct proof of soil or fouling mass.",
        }

    lower = start - timedelta(hours=policy.max_lookback_hours)
    if previous_cip_end is not None:
        prev = _parse_ts(previous_cip_end)
        if prev >= start:
            raise ValueError("previous_cip_end must precede the current CIP")
        lower = max(lower, prev)

    prior = [r for r in same_asset if r.end_ts <= start and r.end_ts >= lower]
    if not prior:
        return {
            "cycle_id": cycle["cycle_id"], "asset": asset, "context_status": "NOT_AVAILABLE",
            "campaign": None,
            "issues": [{"code": "NO_PRECEDING_PRODUCTION_WITHIN_LOOKBACK", "severity": "MEDIUM"}],
            "context_features": {}, "fouling_associated_indicators": {},
            "principle": "Production context is descriptive evidence; it is not direct proof of soil or fouling mass.",
        }

    selected = [prior[-1]]
    gap_limit = timedelta(hours=policy.max_inter_run_gap_hours)
    for run in reversed(prior[:-1]):
        earliest = selected[0]
        if earliest.start_ts - run.end_ts <= gap_limit:
            selected.insert(0, run)
        else:
            break

    first, last = selected[0], selected[-1]
    durations_h = [(r.end_ts - r.start_ts).total_seconds() / 3600 for r in selected]
    total_prod_h = sum(durations_h)
    span_h = (last.end_ts - first.start_ts).total_seconds() / 3600
    internal_idle_h = max(0.0, span_h - total_prod_h)
    pre_cip_idle_h = max(0.0, (start - last.end_ts).total_seconds() / 3600)

    # Prefer measured total volume; otherwise derive a run volume only where a measured
    # average throughput exists. Missing runs stay missing rather than being silently filled.
    run_volumes: list[float | None] = []
    volume_sources: list[str] = []
    for run, dur_h in zip(selected, durations_h):
        if run.metrics.total_volume_l is not None:
            run_volumes.append(run.metrics.total_volume_l)
            volume_sources.append("MEASURED_TOTAL")
        elif run.metrics.average_throughput_lph is not None:
            run_volumes.append(run.metrics.average_throughput_lph * dur_h)
            volume_sources.append("DERIVED_FROM_AVERAGE_THROUGHPUT_X_DURATION")
        else:
            run_volumes.append(None)
            volume_sources.append("UNKNOWN")
    volume_coverage = sum(v is not None for v in run_volumes) / len(run_volumes)
    total_volume = sum(v for v in run_volumes if v is not None) if volume_coverage == 1.0 else None

    def weighted_metric(name: str) -> tuple[float | None, float]:
        vals: list[tuple[float, float]] = []
        present = 0
        for idx, (run, dur_h) in enumerate(zip(selected, durations_h)):
            v = _metric(run, name)
            if v is None:
                continue
            present += 1
            weight = run_volumes[idx] if run_volumes[idx] is not None else dur_h
            vals.append((float(v), float(weight)))
        return _weighted_average(vals), present / len(selected)

    fat, fat_cov = weighted_metric("fat_pct")
    protein, protein_cov = weighted_metric("protein_pct")
    solids, solids_cov = weighted_metric("total_solids_pct")
    avg_temp, avg_temp_cov = weighted_metric("process_temperature_avg_c")
    max_temps = [r.metrics.process_temperature_max_c for r in selected if r.metrics.process_temperature_max_c is not None]
    shutdowns = [r.metrics.shutdown_minutes for r in selected if r.metrics.shutdown_minutes is not None]

    product_codes = [r.product_code for r in selected]
    product_families = [r.product_family for r in selected if r.product_family]
    product_changes = sum(1 for a, b in zip(product_codes, product_codes[1:]) if a != b)
    family = product_families[0] if product_families and len(set(product_families)) == 1 else None

    pressure_change = None
    if first.metrics.pressure_drop_start_bar is not None and last.metrics.pressure_drop_end_bar is not None:
        pressure_change = last.metrics.pressure_drop_end_bar - first.metrics.pressure_drop_start_bar
    ht_decline = None
    if first.metrics.normalized_heat_transfer_start is not None and last.metrics.normalized_heat_transfer_end is not None:
        ht_decline = (first.metrics.normalized_heat_transfer_start - last.metrics.normalized_heat_transfer_end) / first.metrics.normalized_heat_transfer_start * 100.0

    features = {
        "production.total_duration_hours": {"value": total_prod_h, "unit": "h", "evidence_class": "DERIVED"},
        "production.campaign_span_hours": {"value": span_h, "unit": "h", "evidence_class": "DERIVED"},
        "production.internal_idle_hours": {"value": internal_idle_h, "unit": "h", "evidence_class": "DERIVED"},
        "production.pre_cip_idle_hours": {"value": pre_cip_idle_h, "unit": "h", "evidence_class": "DERIVED"},
        "production.run_count": {"value": float(len(selected)), "unit": "count", "evidence_class": "DERIVED"},
        "production.product_change_count": {"value": float(product_changes), "unit": "count", "evidence_class": "DERIVED"},
    }
    if total_volume is not None:
        features["production.total_volume_l"] = {"value": total_volume, "unit": "L", "evidence_class": "DERIVED", "coverage": volume_coverage}
    for key, value, unit, cov in (
        ("production.weighted_fat_pct", fat, "%", fat_cov),
        ("production.weighted_protein_pct", protein, "%", protein_cov),
        ("production.weighted_total_solids_pct", solids, "%", solids_cov),
        ("production.weighted_process_temperature_avg_c", avg_temp, "C", avg_temp_cov),
    ):
        if value is not None:
            features[key] = {"value": value, "unit": unit, "evidence_class": "DERIVED", "coverage": cov}
    if max_temps:
        features["production.process_temperature_max_c"] = {"value": max(max_temps), "unit": "C", "evidence_class": "DERIVED", "coverage": len(max_temps) / len(selected)}
    if shutdowns and len(shutdowns) == len(selected):
        features["production.shutdown_minutes"] = {"value": sum(shutdowns), "unit": "min", "evidence_class": "DERIVED", "coverage": 1.0}
    if pressure_change is not None:
        features["production.pressure_drop_change_bar"] = {"value": pressure_change, "unit": "bar", "evidence_class": "DERIVED"}
    if ht_decline is not None:
        features["production.normalized_heat_transfer_decline_pct"] = {"value": ht_decline, "unit": "%decline", "evidence_class": "DERIVED"}

    issues = []
    if pre_cip_idle_h > policy.long_pre_cip_idle_hours:
        issues.append({
            "code": "LONG_PRE_CIP_IDLE", "severity": "INFO", "hours": round(pre_cip_idle_h, 4),
            "detail": "Long post-production idle time may change cleaning behavior; it is retained as context rather than interpreted as a failure.",
        })
    if volume_coverage < 1.0:
        issues.append({"code": "PARTIAL_PRODUCTION_VOLUME_EVIDENCE", "severity": "INFO", "coverage": round(volume_coverage, 4)})

    indicator_features = {k: v for k, v in features.items() if k in {
        "production.pressure_drop_change_bar", "production.normalized_heat_transfer_decline_pct"
    }}

    return {
        "cycle_id": cycle["cycle_id"], "asset": asset, "context_status": "AVAILABLE",
        "campaign": {
            "start_ts": first.start_ts.isoformat(), "end_ts": last.end_ts.isoformat(),
            "run_ids": [r.run_id for r in selected], "run_count": len(selected),
            "product_codes": product_codes, "product_families": sorted(set(product_families)),
            "dominant_product_family": family, "batch_refs": [r.batch_ref for r in selected if r.batch_ref],
            "source_types": sorted(set(r.source_type for r in selected)),
            "volume_sources": volume_sources,
        },
        "context_features": features,
        "fouling_associated_indicators": indicator_features,
        "issues": issues,
        "principle": "Production context and fouling-associated process indicators are evidence about what preceded CIP; they are not direct measurements of residual soil or microbiological cleanliness.",
    }


def build_context_baseline(*, name: str, revision: str, asset: str, recipe_name: str, recipe_revision: str,
                           candidates: list[dict], policy: ContextPolicy, description: str | None = None) -> dict:
    eligible = [c for c in candidates if c.get("eligible") and c.get("context", {}).get("context_status") == "AVAILABLE"]
    if len(eligible) < policy.minimum_training_cycles:
        raise ValueError(f"context baseline requires at least {policy.minimum_training_cycles} eligible contextual cycles; only {len(eligible)} were available")

    context_values: dict[str, list[float]] = defaultdict(list)
    context_units: dict[str, str | None] = {}
    for c in eligible:
        for key, item in c["context"].get("context_features", {}).items():
            val = _value(item)
            if val is not None:
                context_values[key].append(val)
                context_units.setdefault(key, item.get("unit"))
    context_distributions = {
        key: {**_distribution(vals, context_units.get(key), floors=CONTEXT_SCALE_FLOORS), "training_values": vals}
        for key, vals in context_values.items()
        if len(vals) >= policy.minimum_comparable_cycles
    }
    if len(context_distributions) < policy.minimum_shared_context_features:
        raise ValueError("context baseline does not have enough consistently populated production features")

    lineage = [{
        "cycle_id": c["cycle_id"], "ingestion_id": c.get("ingestion_id"), "start_ts": c.get("start_ts"),
        "run_ids": c.get("context", {}).get("campaign", {}).get("run_ids", []),
    } for c in eligible]
    starts = sorted(str(c["start_ts"]) for c in eligible if c.get("start_ts"))
    cases = []
    for c in eligible:
        cases.append({
            "cycle_id": c["cycle_id"], "ingestion_id": c.get("ingestion_id"), "start_ts": c.get("start_ts"),
            "product_family": c.get("context", {}).get("campaign", {}).get("dominant_product_family"),
            "product_codes": c.get("context", {}).get("campaign", {}).get("product_codes", []),
            "context_features": c["context"].get("context_features", {}),
            "cip_scalars": c["cip_features"].get("scalars", {}),
        })
    return {
        "name": name, "revision": revision, "asset": asset,
        "recipe": {"name": recipe_name, "revision": recipe_revision},
        "description": description,
        "engine": "cip-production-context-baseline", "engine_version": ENGINE_VERSION,
        "training_cycle_count": len(eligible),
        "training_period": {"start": starts[0] if starts else None, "end": starts[-1] if starts else None},
        "context_distributions": context_distributions,
        "cases": cases,
        "policy": policy.model_dump(mode="json"),
        "training_lineage": lineage,
        "lineage_sha256": hashlib.sha256(json.dumps(lineage, sort_keys=True).encode()).hexdigest(),
        "limitations": [
            "L4 similarity is associative, not causal; a production context can explain a pattern without proving why it occurred.",
            "The model does not infer a direct soil mass or microbiological state from production variables.",
            "Comparable-cycle analysis requires historical L2-compliant cycles for the same asset and recipe revision.",
        ],
    }


def _pairwise_context_distance(current: dict, case: dict, baseline: dict, policy: ContextPolicy) -> tuple[float | None, int, list[dict]]:
    components = []
    for key, dist in baseline["context_distributions"].items():
        a = _value(current.get("context_features", {}).get(key))
        b = _value(case.get("context_features", {}).get(key))
        if a is None or b is None:
            continue
        scale = float(dist["robust_scale"])
        z = abs(a - b) / scale if scale > 0 else 0.0
        components.append({"feature": key, "distance_component": z, "current": a, "historical": b, "unit": dist.get("unit")})
    if len(components) < policy.minimum_shared_context_features:
        return None, len(components), components
    distance = math.sqrt(sum(x["distance_component"] ** 2 for x in components) / len(components))
    return distance, len(components), components


def evaluate_context(current_context: dict, current_cip_features: dict, baseline: dict, *, l2_assessment: str) -> dict:
    policy = ContextPolicy.model_validate(baseline["policy"])
    if current_context.get("asset") != baseline["asset"] or current_cip_features.get("asset") != baseline["asset"]:
        raise ValueError("context baseline asset does not match current cycle")
    cycle_id = current_context["cycle_id"]
    trained = {x.get("cycle_id") for x in baseline.get("training_lineage", [])}
    if cycle_id in trained:
        return _not_eval(current_context, baseline, l2_assessment, "Current cycle belongs to the selected L4 training set; self-comparison is withheld.")
    end_text = baseline.get("training_period", {}).get("end")
    if end_text and current_cip_features.get("start_ts"):
        if _parse_ts(current_cip_features["start_ts"]) <= _parse_ts(end_text):
            return _not_eval(current_context, baseline, l2_assessment, "Selected L4 baseline contains same-time or future observations; historical scoring is withheld to prevent look-ahead bias.")
    if l2_assessment == "DATA_REVIEW_REQUIRED":
        return _not_eval(current_context, baseline, l2_assessment, "L4 behavioral interpretation is withheld because L2 reported insufficient/unreliable cleaning evidence.")
    if current_context.get("context_status") != "AVAILABLE":
        return _not_eval(current_context, baseline, l2_assessment, f"Production context is {current_context.get('context_status')}; contextual comparison is unavailable.")

    current_family = current_context.get("campaign", {}).get("dominant_product_family")
    scored = []
    for case in baseline["cases"]:
        if policy.require_same_product_family:
            if current_family is None or case.get("product_family") is None or case.get("product_family") != current_family:
                continue
        distance, shared, components = _pairwise_context_distance(current_context, case, baseline, policy)
        if distance is None or distance > policy.max_context_distance:
            continue
        scored.append({"case": case, "distance": distance, "shared_features": shared, "components": components})
    scored.sort(key=lambda x: x["distance"])
    neighbors = scored[: policy.maximum_neighbors]
    if len(neighbors) < policy.minimum_comparable_cycles:
        return {
            "cycle_id": cycle_id, "asset": current_context["asset"], "context_assessment": "INSUFFICIENT_COMPARABLES",
            "l2_assessment": l2_assessment, "comparable_cycle_count": len(neighbors),
            "minimum_required": policy.minimum_comparable_cycles,
            "conclusion": "Production context was reconstructed, but there are not enough historically compliant cycles with sufficiently similar production conditions to make a reliable contextual behavior claim.",
            "context": current_context, "behavior_differences": [],
            "baseline": {"name": baseline["name"], "revision": baseline["revision"]},
        }

    # Build outcome distributions only from the selected comparable cohort.
    scalar_values: dict[str, list[float]] = defaultdict(list)
    scalar_units: dict[str, str | None] = {}
    for neighbor in neighbors:
        for key, item in neighbor["case"].get("cip_scalars", {}).items():
            val = _value(item)
            if val is not None:
                scalar_values[key].append(val)
                scalar_units.setdefault(key, item.get("unit"))

    diffs = []
    for key, item in current_cip_features.get("scalars", {}).items():
        current = _value(item)
        vals = scalar_values.get(key, [])
        if current is None or len(vals) < policy.minimum_comparable_cycles:
            continue
        dist = _distribution(vals, scalar_units.get(key), floors=BEHAVIOR_SCALE_FLOORS)
        signed = (current - dist["median"]) / dist["robust_scale"] if dist["robust_scale"] > 0 else 0.0
        z = abs(signed)
        if z < policy.warning_robust_z:
            continue
        diffs.append({
            "feature": key, "current": current, "unit": item.get("unit"),
            "comparable_median": dist["median"], "comparable_q1": dist["q1"], "comparable_q3": dist["q3"],
            "robust_z": round(z, 3), "direction": "HIGH" if signed > 0 else "LOW",
            "severity": "HIGH" if z >= policy.high_robust_z else "WARNING",
            "finding_class": "INFERRED",
        })
    diffs.sort(key=lambda x: x["robust_z"], reverse=True)
    assessment = "CONTEXTUALLY_UNUSUAL" if diffs else "CONTEXTUALLY_TYPICAL"
    distances = [x["distance"] for x in neighbors]
    return {
        "cycle_id": cycle_id, "asset": current_context["asset"], "context_assessment": assessment,
        "l2_assessment": l2_assessment,
        "comparable_cycle_count": len(neighbors),
        "comparable_cycle_ids": [x["case"]["cycle_id"] for x in neighbors],
        "similarity": {
            "nearest_distance": round(min(distances), 4), "median_distance": round(statistics.median(distances), 4),
            "maximum_accepted_distance": policy.max_context_distance,
            "distance_is_probability": False,
        },
        "context": current_context,
        "behavior_differences": diffs[: policy.maximum_reported_differences],
        "conclusion": (
            "Current CIP behavior is consistent with historically compliant cycles that followed similar production conditions. This contextual consistency does not prove causation."
            if assessment == "CONTEXTUALLY_TYPICAL" else
            "Current CIP behavior differs from historically compliant cycles with similar production conditions. Production context alone does not establish the cause; investigation is warranted."
        ),
        "baseline": {"name": baseline["name"], "revision": baseline["revision"], "training_cycle_count": baseline["training_cycle_count"]},
        "principle": "L4 asks whether cleaning behavior is unusual given what happened before cleaning; it does not infer cleanliness or causation from production context alone.",
    }


def _not_eval(context: dict, baseline: dict, l2: str, conclusion: str) -> dict:
    return {
        "cycle_id": context.get("cycle_id"), "asset": context.get("asset"), "context_assessment": "NOT_EVALUABLE",
        "l2_assessment": l2, "comparable_cycle_count": 0, "conclusion": conclusion,
        "context": context, "behavior_differences": [],
        "baseline": {"name": baseline["name"], "revision": baseline["revision"]},
    }
