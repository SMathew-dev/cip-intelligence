from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any

from .models import CostProfile, ResourcePolicy

ENGINE_VERSION = "0.1.0"

# These are utility/resource signals, intentionally separate from process-loop hydraulics.
RESOURCE_SIGNALS = {
    "cip.utility.fresh_water.flow": {"quantity": "water_m3", "rate_unit": "L/min", "quantity_unit": "m3", "factor_per_second": 1 / 60000},
    "cip.utility.wastewater.flow": {"quantity": "wastewater_m3", "rate_unit": "L/min", "quantity_unit": "m3", "factor_per_second": 1 / 60000},
    "cip.utility.electric.power": {"quantity": "electricity_kwh", "rate_unit": "kW", "quantity_unit": "kWh", "factor_per_second": 1 / 3600},
    "cip.utility.thermal.power": {"quantity": "thermal_energy_kwh", "rate_unit": "kW", "quantity_unit": "kWh", "factor_per_second": 1 / 3600},
    "cip.chemical.caustic.mass_flow": {"quantity": "caustic_kg", "rate_unit": "kg/min", "quantity_unit": "kg", "factor_per_second": 1 / 60},
    "cip.chemical.acid.mass_flow": {"quantity": "acid_kg", "rate_unit": "kg/min", "quantity_unit": "kg", "factor_per_second": 1 / 60},
    "cip.chemical.sanitizer.mass_flow": {"quantity": "sanitizer_kg", "rate_unit": "kg/min", "quantity_unit": "kg", "factor_per_second": 1 / 60},
}

COST_KEYS = {
    "water_m3": "water_cost_per_m3",
    "wastewater_m3": "wastewater_cost_per_m3",
    "electricity_kwh": "electricity_cost_per_kwh",
    "thermal_energy_kwh": "thermal_energy_cost_per_kwh",
    "caustic_kg": "caustic_cost_per_kg",
    "acid_kg": "acid_cost_per_kg",
    "sanitizer_kg": "sanitizer_cost_per_kg",
}


def _parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        ts = value
    else:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ValueError("resource intelligence requires timezone-aware timestamps")
    return ts


def _cycle_bounds(cycle: dict) -> tuple[datetime, datetime, float]:
    start = _parse_ts(cycle["start_ts"])
    end = _parse_ts(cycle["end_ts"])
    seconds = max((end - start).total_seconds(), float(cycle.get("duration_seconds", 0)))
    if seconds <= 0:
        raise ValueError("cycle duration must be positive")
    return start, end, seconds


def _signal_rows(records: list[dict], concept: str, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    rows: list[tuple[datetime, float]] = []
    for r in records:
        if r.get("concept") != concept:
            continue
        if r.get("quality_code", "GOOD") not in {"GOOD", "REDUNDANT"}:
            continue
        value = r.get("value_double")
        if value is None or not math.isfinite(float(value)) or float(value) < 0:
            continue
        ts = _parse_ts(r["ts_utc"])
        if start <= ts <= end:
            rows.append((ts, float(value)))
    rows.sort(key=lambda x: x[0])
    # Duplicate timestamps for one semantic utility signal are withheld instead of averaged.
    if len({t for t, _ in rows}) != len(rows):
        return []
    return rows


def _integrate_rate(rows: list[tuple[datetime, float]], cycle_seconds: float, policy: ResourcePolicy, factor: float) -> dict:
    if len(rows) < 2:
        return {"status": "NOT_EVALUABLE", "coverage": 0.0, "quantity": None, "reason": "fewer than two trustworthy samples"}
    quantity = 0.0
    covered = 0.0
    gap_count = 0
    for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
        dt = (t1 - t0).total_seconds()
        if dt <= 0:
            continue
        if dt > policy.maximum_integration_gap_seconds:
            gap_count += 1
            continue
        quantity += ((v0 + v1) / 2.0) * dt * factor
        covered += dt
    coverage = min(1.0, covered / cycle_seconds)
    status = "MEASURED" if coverage >= policy.minimum_meter_coverage else "NOT_EVALUABLE"
    return {
        "status": status,
        "coverage": round(coverage, 6),
        "quantity": quantity if status == "MEASURED" else None,
        "observed_partial_quantity": quantity,
        "gap_count": gap_count,
        "samples": len(rows),
    }


def calculate_resources(cycle: dict, normalized_records: list[dict], policy: ResourcePolicy | None = None) -> dict:
    policy = policy or ResourcePolicy()
    start, end, cycle_seconds = _cycle_bounds(cycle)
    resources: dict[str, dict] = {}
    for concept, spec in RESOURCE_SIGNALS.items():
        rows = _signal_rows(normalized_records, concept, start, end)
        result = _integrate_rate(rows, cycle_seconds, policy, spec["factor_per_second"])
        resources[spec["quantity"]] = {
            **result,
            "unit": spec["quantity_unit"],
            "source_concept": concept,
            "evidence_class": "DERIVED" if result["status"] == "MEASURED" else "UNKNOWN",
        }

    return {
        "cycle_id": cycle["cycle_id"],
        "asset": cycle["asset"],
        "start_ts": cycle["start_ts"],
        "duration_seconds": cycle_seconds,
        "resources": resources,
        "engine": "cip-resource-accounting",
        "engine_version": ENGINE_VERSION,
        "policy": policy.model_dump(mode="json"),
        "important_boundary": "CIP return/supply circulation flow is never treated as fresh-water consumption unless a dedicated utility/makeup signal is mapped.",
    }


def _median(values: list[float]) -> float:
    return statistics.median(values)


def build_resource_baseline(*, name: str, revision: str, asset: str, recipe_name: str, recipe_revision: str,
                            candidates: list[dict], policy: ResourcePolicy, description: str | None = None) -> dict:
    eligible = [c for c in candidates if c.get("eligible")]
    if len(eligible) < policy.minimum_baseline_cycles:
        raise ValueError(
            f"resource baseline requires at least {policy.minimum_baseline_cycles} eligible compliant cycles; only {len(eligible)} were available"
        )
    training = eligible
    metric_values: dict[str, list[float]] = defaultdict(list)
    for c in training:
        summary = c["summary"]
        metric_values["duration_seconds"].append(float(summary["duration_seconds"]))
        for key, item in summary["resources"].items():
            if item.get("status") == "MEASURED" and item.get("quantity") is not None:
                metric_values[key].append(float(item["quantity"]))

    references = {}
    for key, vals in metric_values.items():
        if len(vals) >= policy.minimum_reference_cycles:
            references[key] = {
                "n": len(vals),
                "median": _median(vals),
                "minimum": min(vals),
                "maximum": max(vals),
                "training_values": vals,
            }
    if "duration_seconds" not in references:
        raise ValueError("resource baseline did not have enough trustworthy common observations")

    lineage = [{"cycle_id": c["cycle_id"], "ingestion_id": c.get("ingestion_id"), "start_ts": c.get("start_ts")} for c in training]
    starts = sorted(str(c.get("start_ts")) for c in training if c.get("start_ts"))
    return {
        "name": name,
        "revision": revision,
        "asset": asset,
        "recipe": {"name": recipe_name, "revision": recipe_revision},
        "description": description,
        "engine": "cip-resource-baseline",
        "engine_version": ENGINE_VERSION,
        "training_cycle_count": len(training),
        "training_period": {"start": starts[0] if starts else None, "end": starts[-1] if starts else None},
        "references": references,
        "policy": policy.model_dump(mode="json"),
        "training_lineage": lineage,
        "lineage_sha256": hashlib.sha256(json.dumps(lineage, sort_keys=True).encode()).hexdigest(),
        "limitations": [
            "Historical medians are comparison references, not validated optimums.",
            "An excess-versus-baseline calculation identifies an engineering review candidate, not guaranteed savings.",
        ],
    }


def evaluate_economics(summary: dict, baseline: dict | None, cost_profile: CostProfile, *, l2_assessment: str) -> dict:
    if baseline and summary["asset"] != baseline["asset"]:
        raise ValueError("resource baseline asset does not match cycle asset")
    if baseline:
        trained_ids = {x.get("cycle_id") for x in baseline.get("training_lineage", [])}
        if summary["cycle_id"] in trained_ids:
            raise ValueError("cycle cannot be economically compared against a baseline containing itself")
        bend = baseline.get("training_period", {}).get("end")
        if bend and summary.get("start_ts") and _parse_ts(summary["start_ts"]) <= _parse_ts(bend):
            raise ValueError("historical economic comparison would use future baseline observations (look-ahead bias)")

    actual_cost_items = []
    total_actual = 0.0
    known_cost_count = 0
    for quantity_key, cost_field in COST_KEYS.items():
        item = summary["resources"].get(quantity_key, {})
        rate = getattr(cost_profile, cost_field)
        if item.get("status") != "MEASURED" or item.get("quantity") is None or rate is None:
            continue
        cost = float(item["quantity"]) * float(rate)
        total_actual += cost
        known_cost_count += 1
        actual_cost_items.append({"resource": quantity_key, "quantity": item["quantity"], "unit": item["unit"], "rate": rate, "cost": cost})

    opportunities = []
    total_opportunity = 0.0
    optimization_blocked_reason = None
    if l2_assessment != "COMPLIANT":
        optimization_blocked_reason = f"L2 assessment is {l2_assessment}; resource-reduction optimization is withheld until validated process compliance is established."
    if baseline and optimization_blocked_reason is None:
        threshold = ResourcePolicy.model_validate(baseline["policy"]).excessive_threshold_fraction
        for quantity_key, cost_field in COST_KEYS.items():
            current = summary["resources"].get(quantity_key, {})
            ref = baseline["references"].get(quantity_key)
            rate = getattr(cost_profile, cost_field)
            if current.get("status") != "MEASURED" or current.get("quantity") is None or not ref:
                continue
            median = float(ref["median"])
            excess = max(0.0, float(current["quantity"]) - median)
            if excess <= max(abs(median) * threshold, 1e-12):
                continue
            cost = excess * rate if rate is not None else None
            if cost is not None:
                total_opportunity += cost
            opportunities.append({
                "type": "RESOURCE_EXCESS_VS_HISTORICAL_MEDIAN",
                "resource": quantity_key,
                "actual": current["quantity"],
                "historical_median": median,
                "excess": excess,
                "unit": current["unit"],
                "cost_opportunity": cost,
                "finding_class": "DERIVED",
                "claim_strength": "OPTIMIZATION_CANDIDATE",
            })

        duration_ref = baseline["references"].get("duration_seconds")
        if duration_ref:
            excess_seconds = max(0.0, float(summary["duration_seconds"]) - float(duration_ref["median"]))
            if excess_seconds > float(duration_ref["median"]) * threshold:
                capacity_cost = None
                if cost_profile.incremental_production_value_per_hour is not None:
                    capacity_cost = excess_seconds / 3600 * cost_profile.incremental_production_value_per_hour
                    total_opportunity += capacity_cost
                opportunities.append({
                    "type": "EXCESS_CIP_TIME_VS_HISTORICAL_MEDIAN",
                    "actual_seconds": summary["duration_seconds"],
                    "historical_median_seconds": duration_ref["median"],
                    "excess_seconds": excess_seconds,
                    "recoverable_capacity_hours_candidate": excess_seconds / 3600,
                    "capacity_value_opportunity": capacity_cost,
                    "finding_class": "DERIVED",
                    "claim_strength": "OPTIMIZATION_CANDIDATE",
                })

    annualized = None
    if cost_profile.annual_cycles is not None and opportunities:
        annualized = total_opportunity * cost_profile.annual_cycles

    return {
        "cycle_id": summary["cycle_id"],
        "asset": summary["asset"],
        "currency": cost_profile.currency,
        "actual_resource_cost": round(total_actual, 6) if known_cost_count else None,
        "actual_cost_items": actual_cost_items,
        "optimization_candidates": opportunities,
        "optimization_blocked_reason": optimization_blocked_reason,
        "l2_assessment": l2_assessment,
        "per_cycle_opportunity": round(total_opportunity, 6) if opportunities else 0.0,
        "annualized_opportunity_scenario": round(annualized, 6) if annualized is not None else None,
        "cost_profile": cost_profile.model_dump(mode="json"),
        "baseline": {"name": baseline["name"], "revision": baseline["revision"]} if baseline else None,
        "engine": "cip-resource-economics",
        "engine_version": ENGINE_VERSION,
        "limitations": [
            "Costs are only as accurate as plant-configured marginal rates and trustworthy utility/resource measurements.",
            "Historical median is not a validated minimum or proven optimum.",
            "Opportunity values are engineering review candidates, not guaranteed savings.",
            "Production-capacity value is applied only to excess CIP time versus the selected reference, never to the entire necessary CIP duration.",
        ],
    }
