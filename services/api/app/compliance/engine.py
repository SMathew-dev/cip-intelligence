from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from app.reconstruction.models import SignalPoint

from .models import ComplianceRequirement, Condition, ValidatedRecipe

ENGINE_VERSION = "0.1.0"

METRIC_TO_CONCEPT = {
    "return_temperature_c": "cip.return.temperature",
    "return_flow_lpm": "cip.return.flow",
    "return_conductivity_mscm": "cip.return.conductivity",
    "return_pressure_bar": "cip.return.pressure",
}


@dataclass(frozen=True)
class SampleInterval:
    point: SignalPoint
    seconds: float


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ValueError("cycle timestamps must be timezone-aware")
    return ts


def _condition_passes(value: float, condition: Condition) -> bool:
    if not math.isfinite(value):
        return False
    if condition.operator == "gte":
        return value >= float(condition.minimum)
    if condition.operator == "lte":
        return value <= float(condition.maximum)
    return float(condition.minimum) <= value <= float(condition.maximum)


def _condition_text(condition: Condition) -> str:
    if condition.operator == "gte":
        return f"{condition.metric} >= {condition.minimum} {condition.unit}"
    if condition.operator == "lte":
        return f"{condition.metric} <= {condition.maximum} {condition.unit}"
    return f"{condition.minimum} <= {condition.metric} <= {condition.maximum} {condition.unit}"


def _median_interval(points: list[SignalPoint]) -> float:
    gaps = [
        (b.ts - a.ts).total_seconds()
        for a, b in zip(points, points[1:])
        if (b.ts - a.ts).total_seconds() > 0
    ]
    return statistics.median(gaps) if gaps else 0.0


def _intervals(points: list[SignalPoint], phase_end: datetime, max_factor: float) -> list[SampleInterval]:
    if not points:
        return []
    median = _median_interval(points)
    if median <= 0:
        # One point cannot establish dwell/exposure.
        return [SampleInterval(points[0], 0.0)]
    cap = median * max_factor
    result: list[SampleInterval] = []
    for i, point in enumerate(points):
        next_ts = points[i + 1].ts if i + 1 < len(points) else phase_end
        raw = max(0.0, (next_ts - point.ts).total_seconds())
        result.append(SampleInterval(point, min(raw, cap)))
    return result


def _metric_usable(point: SignalPoint, metric: str) -> bool:
    value = getattr(point, metric)
    if value is None or not math.isfinite(value):
        return False
    concept = METRIC_TO_CONCEPT[metric]
    # REDUNDANT is usable only when IO reconciliation produced an agreed value.
    quality = point.quality.get(concept, "GOOD")
    return quality in {"GOOD", "REDUNDANT"}


def _flatline_detail(points: list[SignalPoint], metric: str, phase_end: datetime, max_factor: float) -> dict[str, Any]:
    intervals = _intervals(points, phase_end, max_factor)
    best = {"duration_seconds": 0.0, "value": None, "start_index": None}
    current = 0.0
    current_value: float | None = None
    current_start: int | None = None
    for idx, item in enumerate(intervals):
        if not _metric_usable(item.point, metric):
            current = 0.0
            current_value = None
            current_start = None
            continue
        value = float(getattr(item.point, metric))
        if current_value is not None and abs(value - current_value) <= 1e-12:
            current += item.seconds
        else:
            current_value = value
            current = item.seconds
            current_start = idx
        if current > best["duration_seconds"]:
            best = {"duration_seconds": current, "value": value, "start_index": current_start}
    if best["start_index"] is not None:
        best["prefix_seconds"] = round(sum(i.seconds for i in intervals[:best["start_index"]]), 3)
    else:
        best["prefix_seconds"] = 0.0
    return best


def _phase_for_requirement(cycle: dict, requirement: ComplianceRequirement) -> dict | None:
    matches = [p for p in cycle.get("phases", []) if p.get("phase") == requirement.phase]
    if len(matches) != 1:
        return None
    return matches[0]


def _points_for_phase(points: Iterable[SignalPoint], asset: str, phase: dict) -> list[SignalPoint]:
    start = _parse_ts(phase["start_ts"])
    end = _parse_ts(phase["end_ts"])
    return sorted(
        [p for p in points if p.asset == asset and start <= p.ts <= end],
        key=lambda p: p.ts,
    )


def _not_evaluable(req: ComplianceRequirement, conclusion: str, evidence: dict[str, Any]) -> dict:
    return {
        "code": req.code,
        "title": req.title,
        "phase": req.phase,
        "kind": req.kind,
        "status": "NOT_EVALUABLE",
        "finding_class": "UNKNOWN",
        "severity": "WARNING",
        "conclusion": conclusion,
        "confidence": None,
        "evidence": evidence,
    }


def _pass_fail(req: ComplianceRequirement, passed: bool, conclusion: str, evidence: dict[str, Any], evidence_quality: float) -> dict:
    return {
        "code": req.code,
        "title": req.title,
        "phase": req.phase,
        "kind": req.kind,
        "status": "PASS" if passed else "FAIL",
        "finding_class": "DERIVED",
        "severity": "OK" if passed else "CRITICAL",
        "conclusion": conclusion,
        "confidence": round(max(0.0, min(1.0, evidence_quality)), 3),
        "evidence": evidence,
    }


def _coverage_and_flags(
    intervals: list[SampleInterval],
    conditions: list[Condition],
    phase_duration: float,
    recipe: ValidatedRecipe,
    req: ComplianceRequirement,
    phase_end: datetime,
) -> tuple[float, dict[str, Any], str | None]:
    required_metrics = sorted({c.metric for c in conditions})
    usable_seconds = 0.0
    for item in intervals:
        if all(_metric_usable(item.point, metric) for metric in required_metrics):
            usable_seconds += item.seconds
    coverage = (usable_seconds / phase_duration) if phase_duration > 0 else 0.0

    flatlines = []
    blocking_flatlines = []
    metric_points = [i.point for i in intervals]
    for metric in required_metrics:
        detail = _flatline_detail(
            metric_points,
            metric,
            phase_end,
            recipe.policy.max_sample_interval_factor,
        )
        if detail["duration_seconds"] < recipe.policy.flatline_duration_seconds:
            continue
        flag = {"metric": metric, **detail}
        flatlines.append(flag)

        allowed_stable_endpoint = False
        if req.flatline_policy == "ALLOW_STABLE_ENDPOINT" and req.kind == "ENDPOINT":
            condition = next((c for c in conditions if c.metric == metric), None)
            prefix_items = intervals[: int(detail["start_index"] or 0)]
            prefix_vals = [
                float(getattr(i.point, metric))
                for i in prefix_items
                if _metric_usable(i.point, metric)
            ]
            has_dynamic_prefix = (
                detail["prefix_seconds"] >= max(60.0, phase_duration * 0.10)
                and len(prefix_vals) >= 3
                and (max(prefix_vals) - min(prefix_vals)) > 0.1
            )
            endpoint_value_ok = condition is not None and detail["value"] is not None and _condition_passes(float(detail["value"]), condition)
            allowed_stable_endpoint = bool(has_dynamic_prefix and endpoint_value_ok)
            flag["accepted_as_stable_endpoint"] = allowed_stable_endpoint

        if not allowed_stable_endpoint:
            blocking_flatlines.append(flag)

    evidence = {
        "required_metrics": required_metrics,
        "usable_seconds": round(usable_seconds, 3),
        "phase_duration_seconds": round(phase_duration, 3),
        "data_coverage": round(coverage, 4),
        "flatline_flags": flatlines,
    }
    if blocking_flatlines:
        evidence["blocking_flatline_flags"] = blocking_flatlines
        return coverage, evidence, "required signal contains a suspicious flatline"
    return coverage, evidence, None


def evaluate_requirement(
    cycle: dict,
    points: list[SignalPoint],
    recipe: ValidatedRecipe,
    req: ComplianceRequirement,
) -> dict:
    phase = _phase_for_requirement(cycle, req)
    if phase is None:
        return _not_evaluable(req, "Required phase was not uniquely established in the reconstructed cycle.", {})

    phase_conf = float(phase.get("confidence") or 0.0)
    source = phase.get("evidence_source")
    require_explicit = recipe.policy.allow_inferred_phase_for_compliance is False
    if req.require_explicit_phase is not None:
        require_explicit = req.require_explicit_phase
    if require_explicit and source != "EXPLICIT":
        return _not_evaluable(
            req,
            "Validated compliance was withheld because this phase was inferred rather than explicitly recorded by the plant control/data system.",
            {"phase_evidence_source": source, "phase_confidence": phase_conf},
        )
    if phase_conf < recipe.policy.minimum_phase_confidence:
        return _not_evaluable(
            req,
            "Validated compliance was withheld because phase-reconstruction confidence is below the recipe policy threshold.",
            {"phase_confidence": phase_conf, "required": recipe.policy.minimum_phase_confidence},
        )

    phase_start = _parse_ts(phase["start_ts"])
    phase_end_sample = _parse_ts(phase["end_ts"])
    phase_duration = float(phase.get("duration_seconds") or 0.0)
    # Reconstruction duration includes an estimated final sample interval. Derive a
    # logical phase end from start + duration so exposure accounting matches it.
    phase_end = phase_start + __import__("datetime").timedelta(seconds=phase_duration)
    phase_points = _points_for_phase(points, cycle["asset"], phase)

    base_evidence = {
        "recipe": {"name": recipe.name, "revision": recipe.revision, "approval_ref": recipe.approval_ref},
        "phase_evidence_source": source,
        "phase_confidence": phase_conf,
        "phase_start": phase_start.isoformat(),
        "phase_last_sample": phase_end_sample.isoformat(),
        "phase_duration_seconds": round(phase_duration, 3),
    }

    if req.kind == "PHASE_DURATION":
        passed = phase_duration >= float(req.minimum_seconds)
        evidence = {**base_evidence, "actual_seconds": round(phase_duration, 3), "minimum_seconds": req.minimum_seconds}
        return _pass_fail(
            req,
            passed,
            (
                f"Phase duration requirement achieved: {phase_duration:.1f} s >= {req.minimum_seconds:.1f} s."
                if passed
                else f"Phase duration requirement not achieved: {phase_duration:.1f} s < {req.minimum_seconds:.1f} s."
            ),
            evidence,
            phase_conf,
        )

    intervals = _intervals(phase_points, phase_end, recipe.policy.max_sample_interval_factor)
    coverage, quality_evidence, quality_error = _coverage_and_flags(intervals, req.conditions, phase_duration, recipe, req, phase_end)
    evidence = {**base_evidence, **quality_evidence, "conditions": [_condition_text(c) for c in req.conditions]}
    if quality_error:
        return _not_evaluable(req, f"Requirement cannot be evaluated reliably because a {quality_error}.", evidence)
    if coverage < req.minimum_data_coverage:
        evidence["minimum_data_coverage"] = req.minimum_data_coverage
        return _not_evaluable(
            req,
            f"Requirement cannot be evaluated reliably because data coverage is {coverage:.1%}, below the required {req.minimum_data_coverage:.1%}.",
            evidence,
        )

    def interval_state(item: SampleInterval) -> str:
        if not all(_metric_usable(item.point, c.metric) for c in req.conditions):
            return "UNKNOWN"
        return "PASS" if all(
            _condition_passes(float(getattr(item.point, c.metric)), c)
            for c in req.conditions
        ) else "FAIL"

    if req.kind == "CONTINUOUS_LIMIT":
        states = [(item, interval_state(item)) for item in intervals]
        violating = sum(item.seconds for item, state in states if state == "FAIL")
        unknown = sum(item.seconds for item, state in states if state == "UNKNOWN")
        evidence.update({
            "violating_seconds": round(violating, 3),
            "unknown_seconds": round(unknown, 3),
            "allowed_excursion_seconds": req.allowed_excursion_seconds,
        })
        if violating > req.allowed_excursion_seconds:
            return _pass_fail(
                req, False,
                f"Continuous limit not achieved; observed violating exposure {violating:.1f} s exceeds the allowed {req.allowed_excursion_seconds:.1f} s.",
                evidence, min(phase_conf, coverage),
            )
        if violating + unknown > req.allowed_excursion_seconds:
            return _not_evaluable(
                req,
                "Continuous compliance cannot be proven because unobserved time could exceed the allowed excursion even though observed values do not prove a deviation.",
                evidence,
            )
        return _pass_fail(
            req, True,
            f"Continuous limit achieved; observed violating exposure {violating:.1f} s plus uncertainty remains within the allowed {req.allowed_excursion_seconds:.1f} s.",
            evidence, min(phase_conf, coverage),
        )

    if req.kind in {"QUALIFIED_EXPOSURE", "CONCURRENT_EXPOSURE"}:
        states = [(item, interval_state(item)) for item in intervals]
        qualified = sum(item.seconds for item, state in states if state == "PASS")
        unknown = sum(item.seconds for item, state in states if state == "UNKNOWN")
        minimum = float(req.minimum_seconds)
        evidence.update({
            "qualified_seconds": round(qualified, 3),
            "unknown_seconds": round(unknown, 3),
            "minimum_qualified_seconds": minimum,
        })
        if qualified >= minimum:
            return _pass_fail(
                req, True, f"Qualified exposure achieved: {qualified:.1f} s >= {minimum:.1f} s.",
                evidence, min(phase_conf, coverage),
            )
        if qualified + unknown >= minimum:
            return _not_evaluable(
                req,
                f"Qualified exposure cannot be proven: {qualified:.1f} s is observed compliant, and {unknown:.1f} s is unobserved; the missing evidence could change the outcome.",
                evidence,
            )
        return _pass_fail(
            req, False,
            f"Qualified exposure not achieved: only {qualified:.1f} s is observed compliant, and even treating all {unknown:.1f} s of unknown time as compliant would remain below {minimum:.1f} s.",
            evidence, min(phase_conf, coverage),
        )

    # ENDPOINT: the configured condition must be continuously established at the
    # phase tail. Missing tail evidence yields UNKNOWN; a measured out-of-limit tail
    # yields FAIL; enough measured compliant tail yields PASS.
    hold_required = float(req.endpoint_hold_seconds)
    tail_hold = 0.0
    endpoint_state = "FAIL"
    for item in reversed(intervals):
        if item.seconds <= 0:
            continue
        state = interval_state(item)
        if state == "PASS":
            tail_hold += item.seconds
            if tail_hold >= hold_required:
                endpoint_state = "PASS"
                break
            continue
        if state == "UNKNOWN":
            endpoint_state = "UNKNOWN"
            break
        endpoint_state = "FAIL"
        break

    evidence.update({"tail_hold_seconds": round(tail_hold, 3), "required_hold_seconds": hold_required})
    if endpoint_state == "PASS":
        return _pass_fail(
            req, True,
            f"Endpoint achieved and sustained with measured evidence for {tail_hold:.1f} s (required {hold_required:.1f} s).",
            evidence, min(phase_conf, coverage),
        )
    if endpoint_state == "UNKNOWN":
        return _not_evaluable(
            req,
            f"Endpoint compliance cannot be proven because the final continuous evidence becomes unavailable before the required {hold_required:.1f} s hold is established.",
            evidence,
        )
    return _pass_fail(
        req, False,
        f"Endpoint was measurably outside the configured condition before the required {hold_required:.1f} s tail hold was established; observed compliant tail was {tail_hold:.1f} s.",
        evidence, min(phase_conf, coverage),
    )


def evaluate_cycle(cycle: dict, points: list[SignalPoint], recipe: ValidatedRecipe) -> dict:
    cycle_start = _parse_ts(cycle["start_ts"])
    if cycle["asset"] != recipe.asset:
        raise ValueError(f"recipe asset {recipe.asset!r} does not match cycle asset {cycle['asset']!r}")
    if not (recipe.effective_from <= cycle_start and (recipe.effective_to is None or cycle_start < recipe.effective_to)):
        raise ValueError("recipe revision was not effective when the CIP cycle started")

    if not recipe.approval_ref.strip():
        raise ValueError("validated recipe requires an approval/validation reference")

    results = [evaluate_requirement(cycle, points, recipe, req) for req in recipe.requirements]
    failures = [r for r in results if r["status"] == "FAIL"]
    unknowns = [r for r in results if r["status"] == "NOT_EVALUABLE"]

    if failures:
        overall = "PROCESS_DEVIATION"
    elif unknowns:
        overall = "DATA_REVIEW_REQUIRED"
    else:
        overall = "COMPLIANT"

    return {
        "cycle_id": cycle["cycle_id"],
        "asset": cycle["asset"],
        "recipe": {
            "name": recipe.name,
            "revision": recipe.revision,
            "approval_ref": recipe.approval_ref,
            "effective_from": recipe.effective_from.isoformat(),
            "effective_to": recipe.effective_to.isoformat() if recipe.effective_to else None,
        },
        "engine": "validated-cip-compliance",
        "engine_version": ENGINE_VERSION,
        "overall_assessment": overall,
        "process_deviation": bool(failures),
        "data_review_required": bool(unknowns),
        "requirements_total": len(results),
        "requirements_passed": sum(r["status"] == "PASS" for r in results),
        "requirements_failed": len(failures),
        "requirements_not_evaluable": len(unknowns),
        "findings": results,
        "principle": "Deterministic compliance is separate from probabilistic diagnosis; unknown is allowed.",
    }
