from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any

from app.compliance.models import ValidatedRecipe
from app.reconstruction.models import SignalPoint

from .models import OptimizationPolicy, OutcomeHistorySummary, TrialCycleResult

ENGINE_VERSION = "0.1.0"


def _ts(value: str | datetime) -> datetime:
    d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("L6 requires timezone-aware timestamps")
    return d


def _phase(cycle: dict, name: str) -> dict | None:
    matches = [p for p in cycle.get("phases", []) if p.get("phase") == name]
    return matches[0] if len(matches) == 1 else None


def _condition_ok(point: SignalPoint, condition: dict) -> bool:
    metric = condition["metric"]
    value = getattr(point, metric, None)
    if value is None or not math.isfinite(float(value)):
        return False
    value = float(value)
    op = condition["operator"]
    if op == "lte":
        return value <= float(condition["maximum"])
    if op == "gte":
        return value >= float(condition["minimum"])
    if op == "between":
        return float(condition["minimum"]) <= value <= float(condition["maximum"])
    raise ValueError(f"unsupported endpoint operator {op!r}")


def _endpoint_requirement(recipe: ValidatedRecipe, phase_name: str) -> Any | None:
    reqs = [r for r in recipe.requirements if r.phase == phase_name and r.kind == "ENDPOINT"]
    return reqs[0] if len(reqs) == 1 else None


def _earliest_endpoint_hold(cycle: dict, points: list[SignalPoint], recipe: ValidatedRecipe, phase_name: str = "FINAL_RINSE") -> dict:
    phase = _phase(cycle, phase_name)
    requirement = _endpoint_requirement(recipe, phase_name)
    if not phase or not requirement:
        return {"status": "NOT_EVALUABLE", "reason": "single endpoint-controlled phase/requirement not available"}
    start, end = _ts(phase["start_ts"]), _ts(phase["end_ts"])
    rows = sorted([p for p in points if p.asset == cycle["asset"] and start <= p.ts <= end], key=lambda p: p.ts)
    if len(rows) < 2:
        return {"status": "NOT_EVALUABLE", "reason": "insufficient endpoint samples"}
    hold = float(requirement.endpoint_hold_seconds or 0)
    streak_start: datetime | None = None
    for p in rows:
        if all(_condition_ok(p, c.model_dump(mode="json")) for c in requirement.conditions):
            if streak_start is None:
                streak_start = p.ts
            if (p.ts - streak_start).total_seconds() >= hold:
                achieved = p.ts
                return {
                    "status": "OBSERVED",
                    "requirement_code": requirement.code,
                    "phase": phase_name,
                    "phase_start_ts": phase["start_ts"],
                    "phase_end_ts": phase["end_ts"],
                    "phase_duration_seconds": float(phase["duration_seconds"]),
                    "endpoint_first_satisfied_ts": streak_start.isoformat(),
                    "endpoint_hold_achieved_ts": achieved.isoformat(),
                    "seconds_from_phase_start_to_validated_hold": (achieved - start).total_seconds(),
                    "tail_after_validated_hold_seconds": max(0.0, (end - achieved).total_seconds()),
                    "approved_endpoint_condition_remains_authoritative": True,
                }
        else:
            streak_start = None
    return {"status": "NOT_OBSERVED", "reason": "approved endpoint hold was not observed", "requirement_code": requirement.code}


def summarize_outcome_history(rows: list[dict], *, comparable_cycle_ids: set[str] | None = None) -> OutcomeHistorySummary:
    comparable_cycle_ids = comparable_cycle_ids or {str(r.get("cycle_id")) for r in rows if r.get("cycle_id")}
    scoped = [r for r in rows if r.get("cycle_id") in comparable_cycle_ids]
    by_cycle: dict[str, list[str]] = {}
    for row in scoped:
        cid = str(row.get("cycle_id"))
        if not cid or cid == "None":
            continue
        by_cycle.setdefault(cid, []).append(str(row.get("outcome", "INCONCLUSIVE")).upper())
    passed = failed = inconclusive = 0
    for outcomes in by_cycle.values():
        if "FAIL" in outcomes:
            failed += 1
        elif "BORDERLINE" in outcomes or "INCONCLUSIVE" in outcomes:
            inconclusive += 1
        elif "PASS" in outcomes:
            passed += 1
        else:
            inconclusive += 1
    return OutcomeHistorySummary(
        comparable_cycles=len(comparable_cycle_ids),
        cycles_with_verification=len(by_cycle),
        passed_verifications=passed,
        failed_verifications=failed,
        borderline_or_inconclusive=inconclusive,
    )


def _outcome_eligibility(summary: OutcomeHistorySummary, policy: OptimizationPolicy) -> tuple[bool, list[str], dict]:
    blockers: list[str] = []
    coverage = summary.cycles_with_verification / summary.comparable_cycles if summary.comparable_cycles else 0.0
    decisive = summary.passed_verifications + summary.failed_verifications
    pass_rate = summary.passed_verifications / decisive if decisive else None
    if summary.comparable_cycles < policy.minimum_reference_cycles:
        blockers.append("insufficient comparable historical cycles")
    if summary.cycles_with_verification < policy.minimum_outcome_cycles:
        blockers.append("insufficient historical verification cycles")
    if coverage < policy.minimum_outcome_coverage:
        blockers.append("historical verification coverage below optimization policy")
    if pass_rate is None or pass_rate < policy.minimum_historical_pass_rate:
        blockers.append("historical verification pass rate below optimization policy")
    return not blockers, blockers, {"coverage": round(coverage, 4), "decisive_pass_rate": round(pass_rate, 4) if pass_rate is not None else None}


def discover_final_rinse_candidate(
    cycle: dict,
    points: list[SignalPoint],
    recipe: ValidatedRecipe,
    *,
    compliance: dict,
    behavior_baseline: dict | None,
    economics: dict | None,
    diagnostics: dict | None,
    outcome_history: OutcomeHistorySummary | None,
    policy: OptimizationPolicy | None = None,
) -> dict:
    """Discover a controlled-validation candidate for excess final-rinse tail time.

    This does not authorize a recipe change and does not tell a PLC when to stop.
    """
    policy = policy or OptimizationPolicy()
    blockers: list[str] = []
    warnings: list[str] = []
    l2 = compliance.get("overall_assessment", "NOT_EVALUABLE")
    if l2 != "COMPLIANT":
        blockers.append(f"L2 assessment is {l2}; optimization requires COMPLIANT")
    if cycle.get("reconstruction_mode") != "EXPLICIT" or float(cycle.get("confidence", 0)) < 0.95:
        blockers.append("cycle reconstruction is not high-confidence explicit plant evidence")

    diagnostic = diagnostics or {}
    if policy.block_on_confirmed_unresolved_condition and diagnostic.get("confirmed_conditions"):
        blockers.append("confirmed equipment/process condition is unresolved")
    if policy.block_on_high_diagnostic_hypothesis:
        if any(str(h.get("severity", "")).upper() == "HIGH" for h in diagnostic.get("hypotheses", [])):
            blockers.append("high-severity diagnostic hypothesis is unresolved")
    if any(str(d.get("code", "")).startswith("FM-INS") for d in diagnostic.get("detections", [])):
        blockers.append("instrument/data-quality diagnostic blocks optimization")

    endpoint = _earliest_endpoint_hold(cycle, points, recipe, "FINAL_RINSE")
    if endpoint.get("status") != "OBSERVED":
        blockers.append("validated final-rinse endpoint was not demonstrably achieved")

    phase_dist = None
    reference_cycles = 0
    if behavior_baseline:
        phase_dist = behavior_baseline.get("scalar_features", {}).get("phase.FINAL_RINSE.duration_seconds")
        reference_cycles = int(behavior_baseline.get("training_cycle_count", 0))
        if reference_cycles < policy.minimum_reference_cycles or not phase_dist:
            blockers.append("behavioral reference is too small or lacks final-rinse duration evidence")
    else:
        blockers.append("behavioral reference is unavailable")

    if outcome_history is None and policy.require_qa_for_hygiene_sensitive_change:
        blockers.append("historical QA/verification outcome evidence is unavailable")
        outcome_stats = None
    elif outcome_history is not None:
        ok, outcome_blockers, outcome_stats = _outcome_eligibility(outcome_history, policy)
        if policy.require_qa_for_hygiene_sensitive_change and not ok:
            blockers.extend(outcome_blockers)
    else:
        outcome_stats = None

    phase = _phase(cycle, "FINAL_RINSE")
    current_seconds = float(phase["duration_seconds"]) if phase else None
    target_seconds = None
    potential_reduction = None
    if endpoint.get("status") == "OBSERVED" and phase_dist and current_seconds is not None:
        # A conservative *trial envelope*: stay at/above the historical upper quartile,
        # add a configurable guard band beyond the observed endpoint hold, and cap the
        # size of any single proposed reduction. This is not a new validated recipe.
        historical_q3 = float(phase_dist["q3"])
        endpoint_plus_guard = float(endpoint["seconds_from_phase_start_to_validated_hold"]) + policy.trial_guard_band_seconds
        reduction_floor = current_seconds * (1.0 - policy.maximum_single_trial_reduction_fraction)
        target_seconds = max(historical_q3, endpoint_plus_guard, reduction_floor)
        target_seconds = math.ceil(target_seconds / 10.0) * 10.0
        potential_reduction = max(0.0, current_seconds - target_seconds)
        if float(endpoint["tail_after_validated_hold_seconds"]) < policy.minimum_endpoint_margin_seconds:
            blockers.append("insufficient observed tail time after validated endpoint")
        if potential_reduction < policy.minimum_time_saving_seconds:
            blockers.append("potential time reduction is below optimization policy minimum")
        if target_seconds >= current_seconds:
            blockers.append("no defensible shorter trial envelope was identified")

    econ_candidates = (economics or {}).get("optimization_candidates", [])
    relevant_econ = [x for x in econ_candidates if x.get("type") in {"EXCESS_CIP_TIME_VS_HISTORICAL_MEDIAN", "RESOURCE_EXCESS_VS_HISTORICAL_MEDIAN"}]
    if economics and economics.get("optimization_blocked_reason"):
        blockers.append("resource/economics layer has optimization blocked")
    if not relevant_econ:
        warnings.append("no quantified economics candidate is available; trial may still have engineering value but ROI is not established")

    eligible = len(set(blockers)) == 0
    blockers = sorted(set(blockers))
    candidate_material = {
        "cycle_id": cycle.get("cycle_id"), "asset": cycle.get("asset"), "recipe": [recipe.name, recipe.revision],
        "current": current_seconds, "target": target_seconds, "endpoint": endpoint,
        "baseline_lineage": behavior_baseline.get("lineage_sha256") if behavior_baseline else None,
        "policy": policy.model_dump(mode="json"),
    }
    candidate_id = "opt-" + hashlib.sha256(json.dumps(candidate_material, sort_keys=True, default=str).encode()).hexdigest()[:16]

    return {
        "candidate_id": candidate_id,
        "candidate_type": "FINAL_RINSE_TAIL_REDUCTION_TRIAL",
        "asset": cycle.get("asset"),
        "cycle_id": cycle.get("cycle_id"),
        "recipe": {"name": recipe.name, "revision": recipe.revision, "approval_ref": recipe.approval_ref},
        "eligibility": "ELIGIBLE_FOR_CONTROLLED_VALIDATION" if eligible else "BLOCKED",
        "blockers": blockers,
        "warnings": warnings,
        "finding_class": "DERIVED",
        "claim_strength": "CONTROLLED_VALIDATION_CANDIDATE" if eligible else "NO_RECOMMENDATION",
        "current_final_rinse_seconds": current_seconds,
        "observed_endpoint": endpoint,
        "historical_reference": {
            "training_cycles": reference_cycles,
            "duration_median_seconds": phase_dist.get("median") if phase_dist else None,
            "duration_q3_seconds": phase_dist.get("q3") if phase_dist else None,
            "baseline_name": behavior_baseline.get("name") if behavior_baseline else None,
            "baseline_revision": behavior_baseline.get("revision") if behavior_baseline else None,
        },
        "outcome_evidence": {
            **(outcome_history.model_dump(mode="json") if outcome_history else {}),
            **(outcome_stats or {}),
        } if outcome_history else None,
        "economics_evidence": {
            "per_cycle_opportunity": economics.get("per_cycle_opportunity") if economics else None,
            "annualized_opportunity_scenario": economics.get("annualized_opportunity_scenario") if economics else None,
            "currency": economics.get("currency") if economics else None,
            "candidate_count": len(relevant_econ),
        },
        "proposed_controlled_trial": {
            "nominal_review_target_seconds": target_seconds if eligible else None,
            "potential_tail_reduction_seconds": potential_reduction if eligible else None,
            "critical_boundary": "The approved final-rinse endpoint remains authoritative. A trial must never terminate the rinse merely because the nominal time target is reached if the validated endpoint/hold condition has not been satisfied.",
            "automatic_control_change": False,
            "requires_engineering_approval": True,
            "requires_qa_approval": True,
            "requires_controlled_validation": True,
            "minimum_trial_cycles": policy.minimum_trial_cycles,
        },
        "engine": "cip-controlled-optimization",
        "engine_version": ENGINE_VERSION,
        "policy": policy.model_dump(mode="json"),
        "limitations": [
            "This candidate does not establish a new validated cleaning recipe.",
            "Historical normal behavior is not automatically an optimum or regulatory minimum.",
            "QA outcomes are associated evidence and do not prove microbiological cleanliness from process sensors alone.",
            "CIP Intelligence has no write path to the PLC/HMI and cannot implement the proposed change automatically.",
        ],
    }


def assess_controlled_trial(candidate: dict, results: list[TrialCycleResult], policy: OptimizationPolicy | None = None,
                            *, engineering_approval_ref: str | None = None, qa_approval_ref: str | None = None,
                            protocol_ref: str | None = None) -> dict:
    policy = policy or OptimizationPolicy.model_validate(candidate.get("policy", {}))
    blockers: list[str] = []
    if candidate.get("eligibility") != "ELIGIBLE_FOR_CONTROLLED_VALIDATION":
        blockers.append("candidate was not eligible for controlled validation")
    if not engineering_approval_ref:
        blockers.append("engineering approval reference is missing")
    if not qa_approval_ref:
        blockers.append("QA approval reference is missing")
    if not protocol_ref:
        blockers.append("controlled-validation protocol reference is missing")
    if len(results) < policy.minimum_trial_cycles:
        blockers.append(f"fewer than {policy.minimum_trial_cycles} controlled trial cycles are available")

    deviations = [r for r in results if r.l2_assessment != "COMPLIANT"]
    failures = [r for r in results if r.verification_outcome == "FAIL"]
    unresolved = [r for r in results if r.diagnostic_status in {"HYPOTHESES_AVAILABLE", "CONFIRMED_CONDITION"}]
    qa_missing = [r for r in results if r.verification_outcome == "NOT_AVAILABLE"]

    if deviations:
        blockers.append("one or more trial cycles did not maintain L2 compliance")
    if len(failures) > policy.maximum_trial_failed_verifications:
        blockers.append("trial verification failures exceed the approved policy")
    if unresolved:
        blockers.append("one or more trial cycles contain unresolved diagnostic findings")
    if policy.require_qa_for_hygiene_sensitive_change and qa_missing:
        blockers.append("QA verification is missing for one or more hygiene-sensitive trial cycles")

    total_savings: dict[str, float] = {}
    for r in results:
        for key, value in r.measured_savings.items():
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                total_savings[key] = total_savings.get(key, 0.0) + float(value)

    status = "EVIDENCE_SUPPORTS_HUMAN_REVIEW" if not blockers else ("REJECT_OR_INVESTIGATE" if deviations or failures or unresolved else "INSUFFICIENT_TRIAL_EVIDENCE")
    return {
        "candidate_id": candidate.get("candidate_id"),
        "assessment": status,
        "blockers": sorted(set(blockers)),
        "trial_cycles": len(results),
        "l2_deviation_cycles": len(deviations),
        "verification_failures": len(failures),
        "unresolved_diagnostic_cycles": len(unresolved),
        "aggregate_measured_savings": {k: round(v, 9) for k, v in total_savings.items()},
        "governance": {
            "engineering_approval_ref": engineering_approval_ref,
            "qa_approval_ref": qa_approval_ref,
            "protocol_ref": protocol_ref,
            "automatic_recipe_acceptance": False,
            "required_next_step": "Human engineering/QA review and formal plant change-control/validation decision." if not blockers else "Investigate blockers; do not adopt the proposed change.",
        },
        "engine": "cip-controlled-trial-assessment",
        "engine_version": ENGINE_VERSION,
    }
