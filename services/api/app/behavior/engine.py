from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime
from collections import defaultdict
from typing import Any

from .models import BehaviorPolicy

ENGINE_VERSION = "0.1.0"

# Engineering floors prevent a nearly-zero historical MAD from turning harmless
# numerical noise into absurd z-scores. They are anomaly-analysis guardrails, not
# process limits and never replace plant validation requirements.
SCALE_FLOORS = {
    "s": 5.0,
    "C": 0.20,
    "L/min": 1.5,
    "mS/cm": 0.08,
    "bar": 0.015,
}


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1 - weight) + ordered[hi] * weight


def _distribution(values: list[float], unit: str) -> dict[str, Any]:
    med = statistics.median(values)
    abs_dev = [abs(v - med) for v in values]
    mad = statistics.median(abs_dev)
    q1 = _quantile(values, 0.25)
    q3 = _quantile(values, 0.75)
    robust_sigma = 1.4826 * mad
    iqr_sigma = (q3 - q1) / 1.349 if q3 > q1 else 0.0
    scale_floor = SCALE_FLOORS.get(unit, max(abs(med) * 0.0025, 1e-9))
    scale = max(robust_sigma, iqr_sigma, scale_floor)
    return {
        "n": len(values),
        "median": med,
        "q1": q1,
        "q3": q3,
        "mad": mad,
        "robust_scale": scale,
        "minimum": min(values),
        "maximum": max(values),
        "unit": unit,
    }


def _robust_z(value: float, dist: dict) -> float:
    scale = float(dist["robust_scale"])
    return abs(value - float(dist["median"])) / scale if scale > 0 else 0.0


def _signed_robust_z(value: float, dist: dict) -> float:
    scale = float(dist["robust_scale"])
    return (value - float(dist["median"])) / scale if scale > 0 else 0.0


def _empirical_position(value: float, values: list[float]) -> dict[str, Any]:
    below = sum(v < value for v in values)
    equal = sum(v == value for v in values)
    n = len(values)
    percentile = (below + 0.5 * equal) / n if n else None
    return {
        "baseline_cycles": n,
        "fraction_below": round(below / n, 4) if n else None,
        "fraction_at_or_below_midrank": round(percentile, 4) if percentile is not None else None,
    }


def _collect_scalar_distributions(vectors: list[dict], minimum_feature_cycles: int) -> dict[str, dict]:
    values: dict[str, list[float]] = defaultdict(list)
    metadata: dict[str, dict] = {}
    for vector in vectors:
        for key, item in vector["features"]["scalars"].items():
            value = item.get("value")
            if value is None or not math.isfinite(float(value)):
                continue
            values[key].append(float(value))
            metadata.setdefault(key, {"unit": item.get("unit"), "family": item.get("family")})
    out: dict[str, dict] = {}
    for key, vals in values.items():
        if len(vals) < minimum_feature_cycles:
            continue
        out[key] = {
            **_distribution(vals, metadata[key]["unit"]),
            "family": metadata[key]["family"],
            "training_values": vals,
        }
    return out


def _collect_profile_distributions(vectors: list[dict], minimum_feature_cycles: int, bins: int) -> dict[str, dict]:
    profiles: dict[str, dict] = {}
    all_keys = sorted({key for v in vectors for key in v["features"]["profiles"]})
    for key in all_keys:
        meta = next(v["features"]["profiles"][key] for v in vectors if key in v["features"]["profiles"])
        bin_summaries = []
        for idx in range(bins):
            vals = []
            for vector in vectors:
                p = vector["features"]["profiles"].get(key)
                if not p or idx >= len(p["values"]):
                    continue
                value = p["values"][idx]
                if value is not None and math.isfinite(float(value)):
                    vals.append(float(value))
            if len(vals) >= minimum_feature_cycles:
                summary = _distribution(vals, meta["unit"])
                summary["training_values"] = vals
                bin_summaries.append(summary)
            else:
                bin_summaries.append(None)
        if sum(x is not None for x in bin_summaries) >= max(2, bins // 2):
            profiles[key] = {
                "unit": meta["unit"],
                "family": "profile",
                "bins": bin_summaries,
            }
    return profiles


def _gross_training_outliers(vectors: list[dict], policy: BehaviorPolicy) -> dict[str, list[str]]:
    """Screen gross compliant anomalies from poisoning a future normal baseline.

    This is intentionally only a gross screen. We do not recursively trim until the
    remaining population looks perfect; normal plant variability must remain in the baseline.
    """
    distributions = _collect_scalar_distributions(vectors, policy.minimum_feature_cycles)
    profile_distributions = _collect_profile_distributions(
        vectors, policy.minimum_feature_cycles, policy.profile_bins
    )
    rejected: dict[str, list[str]] = {}
    for vector in vectors:
        reasons: list[tuple[str, float]] = []
        for key, item in vector["features"]["scalars"].items():
            if key not in distributions or item.get("value") is None:
                continue
            z = _robust_z(float(item["value"]), distributions[key])
            if z >= policy.training_screen_robust_z:
                reasons.append((key, z))

        profile_reasons: list[tuple[str, float]] = []
        for key, current_profile in vector["features"]["profiles"].items():
            bprofile = profile_distributions.get(key)
            if not bprofile:
                continue
            deviant_bins: list[int] = []
            max_z = 0.0
            for idx, current in enumerate(current_profile["values"]):
                if current is None or idx >= len(bprofile["bins"]):
                    continue
                dist = bprofile["bins"][idx]
                if dist is None:
                    continue
                z = _robust_z(float(current), dist)
                max_z = max(max_z, z)
                if z >= policy.training_screen_robust_z:
                    deviant_bins.append(idx)
            if _contiguous_true(deviant_bins) >= policy.minimum_profile_adjacent_bins:
                profile_reasons.append((f"profile:{key}", max_z))

        combined = reasons + profile_reasons
        extreme = [x for x in combined if x[1] >= policy.training_screen_extreme_z]
        if extreme or len(combined) >= policy.training_screen_feature_count or profile_reasons:
            selected = extreme if extreme else sorted(combined, key=lambda x: x[1], reverse=True)
            rejected[vector["cycle_id"]] = [f"{key}: robust_z={z:.2f}" for key, z in selected[:6]]
    return rejected


def _baseline_maturity(n: int) -> str:
    if n >= 200:
        return "MATURE"
    if n >= 50:
        return "ESTABLISHED"
    return "DEVELOPING"


def build_baseline(
    *,
    name: str,
    revision: str,
    asset: str,
    recipe_name: str,
    recipe_revision: str,
    candidates: list[dict],
    policy: BehaviorPolicy,
    description: str | None = None,
) -> dict:
    eligible = [c for c in candidates if c.get("eligible") is True]
    excluded = [
        {
            "cycle_id": c.get("cycle_id"),
            "ingestion_id": c.get("ingestion_id"),
            "reason": c.get("eligibility_reason", "not eligible"),
        }
        for c in candidates
        if c.get("eligible") is not True
    ]
    if len(eligible) < policy.minimum_baseline_cycles:
        raise ValueError(
            f"behavior baseline requires at least {policy.minimum_baseline_cycles} eligible compliant cycles; "
            f"only {len(eligible)} were available"
        )

    screened = _gross_training_outliers(eligible, policy)
    training = [c for c in eligible if c["cycle_id"] not in screened]
    for c in eligible:
        if c["cycle_id"] in screened:
            excluded.append({
                "cycle_id": c["cycle_id"],
                "ingestion_id": c.get("ingestion_id"),
                "reason": "gross behavioral outlier screened from baseline training",
                "evidence": screened[c["cycle_id"]],
            })
    if len(training) < policy.minimum_baseline_cycles:
        raise ValueError(
            "gross-outlier screening left too few cycles to create a trustworthy baseline; "
            "review the requested history instead of relaxing the guardrail automatically"
        )

    scalars = _collect_scalar_distributions(training, policy.minimum_feature_cycles)
    profiles = _collect_profile_distributions(training, policy.minimum_feature_cycles, policy.profile_bins)
    if not scalars:
        raise ValueError("eligible cycles did not provide enough common trustworthy features for a baseline")

    lineage = [
        {
            "ingestion_id": c.get("ingestion_id"),
            "cycle_id": c["cycle_id"],
            "start_ts": c.get("start_ts"),
            "compliance_sha256": c.get("compliance_sha256"),
            "reconstruction_sha256": c.get("reconstruction_sha256"),
            "normalized_sha256": c.get("normalized_sha256"),
        }
        for c in training
    ]
    lineage_hash = hashlib.sha256(json.dumps(lineage, sort_keys=True).encode()).hexdigest()

    training_starts = sorted(c.get("start_ts") for c in training if c.get("start_ts"))

    return {
        "name": name,
        "revision": revision,
        "asset": asset,
        "recipe": {"name": recipe_name, "revision": recipe_revision},
        "description": description,
        "engine": "cip-behavioral-baseline",
        "engine_version": ENGINE_VERSION,
        "policy": policy.model_dump(mode="json"),
        "training_cycle_count": len(training),
        "training_period": {
            "start": training_starts[0] if training_starts else None,
            "end": training_starts[-1] if training_starts else None,
        },
        "eligible_before_screening": len(eligible),
        "excluded_cycle_count": len(excluded),
        "baseline_maturity": _baseline_maturity(len(training)),
        "scalar_features": scalars,
        "profile_features": profiles,
        "training_lineage": lineage,
        "excluded_cycles": excluded,
        "lineage_sha256": lineage_hash,
        "limitations": [
            "L3 is asset- and recipe-specific; production/product context is not modeled until L4.",
            "Behavioral deviation is not a process-compliance failure and cannot override L2.",
            "The baseline is immutable and does not silently learn from new cycles.",
        ],
    }


def _contiguous_true(indices: list[int]) -> int:
    if not indices:
        return 0
    best = current = 1
    for a, b in zip(indices, indices[1:]):
        if b == a + 1:
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best


def evaluate_behavior(features: dict, baseline: dict, *, l2_assessment: str) -> dict:
    policy = BehaviorPolicy.model_validate(baseline["policy"])
    if features["asset"] != baseline["asset"]:
        raise ValueError("behavior baseline asset does not match cycle asset")
    trained_cycle_ids = {x.get("cycle_id") for x in baseline.get("training_lineage", [])}
    if features["cycle_id"] in trained_cycle_ids:
        return {
            "cycle_id": features["cycle_id"],
            "asset": features["asset"],
            "behavioral_assessment": "NOT_EVALUABLE",
            "l2_assessment": l2_assessment,
            "conclusion": "Behavioral comparison was withheld because this cycle is part of the selected baseline training set; self-comparison would leak the answer into the reference population.",
            "deviations": [],
            "profile_deviations": [],
            "baseline": {"name": baseline["name"], "revision": baseline["revision"]},
        }
    training_end_text = baseline.get("training_period", {}).get("end")
    if training_end_text and features.get("start_ts"):
        training_end = datetime.fromisoformat(training_end_text.replace("Z", "+00:00"))
        cycle_start = datetime.fromisoformat(str(features["start_ts"]).replace("Z", "+00:00"))
        if cycle_start <= training_end:
            return {
                "cycle_id": features["cycle_id"],
                "asset": features["asset"],
                "behavioral_assessment": "NOT_EVALUABLE",
                "l2_assessment": l2_assessment,
                "conclusion": "Behavioral comparison was withheld because the selected baseline contains observations from the same or a later time period. This prevents look-ahead bias in historical scoring.",
                "deviations": [],
                "profile_deviations": [],
                "baseline": {"name": baseline["name"], "revision": baseline["revision"]},
            }
    if l2_assessment == "DATA_REVIEW_REQUIRED":
        return {
            "cycle_id": features["cycle_id"],
            "asset": features["asset"],
            "behavioral_assessment": "NOT_EVALUABLE",
            "l2_assessment": l2_assessment,
            "conclusion": "Behavioral comparison was withheld because L2 identified insufficient or unreliable evidence.",
            "deviations": [],
            "profile_deviations": [],
            "baseline": {"name": baseline["name"], "revision": baseline["revision"]},
        }

    deviations = []
    for key, item in features["scalars"].items():
        dist = baseline["scalar_features"].get(key)
        if not dist or item.get("value") is None:
            continue
        value = float(item["value"])
        signed_z = _signed_robust_z(value, dist)
        z = abs(signed_z)
        if z < policy.warning_robust_z:
            continue
        position = _empirical_position(value, [float(v) for v in dist.get("training_values", [])])
        deviations.append({
            "feature": key,
            "family": item.get("family"),
            "unit": item.get("unit"),
            "current": value,
            "baseline_median": dist["median"],
            "baseline_q1": dist["q1"],
            "baseline_q3": dist["q3"],
            "robust_z": round(z, 3),
            "direction": "HIGH" if signed_z > 0 else "LOW",
            "severity": "HIGH" if z >= policy.high_robust_z else "WARNING",
            "empirical_position": position,
            "finding_class": "INFERRED",
        })

    profile_deviations = []
    for key, current_profile in features["profiles"].items():
        bprofile = baseline["profile_features"].get(key)
        if not bprofile:
            continue
        warning_bins: list[int] = []
        high_bins: list[int] = []
        bin_details = []
        for idx, current in enumerate(current_profile["values"]):
            if current is None or idx >= len(bprofile["bins"]):
                continue
            dist = bprofile["bins"][idx]
            if dist is None:
                continue
            signed = _signed_robust_z(float(current), dist)
            z = abs(signed)
            if z >= policy.warning_robust_z:
                warning_bins.append(idx)
                if z >= policy.high_robust_z:
                    high_bins.append(idx)
                bin_details.append({
                    "bin": idx,
                    "current": float(current),
                    "baseline_median": dist["median"],
                    "robust_z": round(z, 3),
                    "direction": "HIGH" if signed > 0 else "LOW",
                })
        sustained_warning = _contiguous_true(warning_bins)
        sustained_high = _contiguous_true(high_bins)
        if sustained_warning >= policy.minimum_profile_adjacent_bins:
            profile_deviations.append({
                "profile": key,
                "unit": current_profile.get("unit"),
                "severity": "HIGH" if sustained_high >= policy.minimum_profile_adjacent_bins else "WARNING",
                "deviant_bins": warning_bins,
                "longest_adjacent_run": sustained_warning,
                "bin_evidence": bin_details,
                "finding_class": "INFERRED",
                "conclusion": "A sustained portion of the phase profile differs from this asset/recipe baseline.",
            })

    deviations.sort(key=lambda x: x["robust_z"], reverse=True)
    profile_deviations.sort(
        key=lambda x: max((b["robust_z"] for b in x["bin_evidence"]), default=0),
        reverse=True,
    )
    high = any(d["severity"] == "HIGH" for d in deviations) or any(d["severity"] == "HIGH" for d in profile_deviations)
    warning_count = sum(d["severity"] == "WARNING" for d in deviations) + sum(d["severity"] == "WARNING" for d in profile_deviations)

    if high:
        assessment = "HIGHLY_UNUSUAL"
    elif warning_count >= 1:
        assessment = "UNUSUAL"
    else:
        assessment = "NORMAL"

    l2_note = None
    if l2_assessment == "PROCESS_DEVIATION":
        l2_note = "L2 process deviation remains authoritative; L3 behavior is supplementary and cannot downgrade it."

    top = deviations[: policy.maximum_reported_deviations]
    return {
        "cycle_id": features["cycle_id"],
        "asset": features["asset"],
        "behavioral_assessment": assessment,
        "l2_assessment": l2_assessment,
        "baseline": {
            "name": baseline["name"],
            "revision": baseline["revision"],
            "training_cycle_count": baseline["training_cycle_count"],
            "maturity": baseline["baseline_maturity"],
            "lineage_sha256": baseline["lineage_sha256"],
        },
        "deviation_count": len(deviations),
        "profile_deviation_count": len(profile_deviations),
        "deviations": top,
        "profile_deviations": profile_deviations[: policy.maximum_reported_deviations],
        "conclusion": (
            "Cycle behavior is consistent with the historical asset/recipe baseline."
            if assessment == "NORMAL"
            else "Cycle behavior differs materially from the historical asset/recipe baseline; investigate the evidence rather than treating this as an automatic process failure."
        ),
        "l2_authority_note": l2_note,
        "principle": "L3 detects behavior change; only L2 determines validated process compliance.",
    }
