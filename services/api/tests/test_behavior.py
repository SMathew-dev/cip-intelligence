from __future__ import annotations

import csv
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from app.behavior.engine import build_baseline, evaluate_behavior
from app.behavior.features import extract_behavior_features
from app.behavior.models import BehaviorPolicy
from app.behavior.store import BehaviorBaselineStore
from app.compliance.engine import evaluate_cycle
from app.compliance.models import ValidatedRecipe
from app.reconstruction.engine import reconstruct_cycles
from app.reconstruction.models import SignalPoint
from app.simulator import generate_cycle

REPO_ROOT = Path(__file__).resolve().parents[3]


def recipe() -> ValidatedRecipe:
    return ValidatedRecipe.model_validate_json(
        (REPO_ROOT / "config" / "example_htst_validated_recipe_v7.json").read_text(encoding="utf-8")
    )


def scenario_points(tmp_path: Path, scenario: str, *, seed: int = 7, day: int = 1, explicit: bool = True) -> list[SignalPoint]:
    path = tmp_path / f"{scenario}-{seed}-{day}.csv"
    generate_cycle(path, scenario=scenario, seed=seed, start=recipe().effective_from + timedelta(days=day))
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [
        SignalPoint(
            ts=__import__("datetime").datetime.fromisoformat(r["timestamp"]),
            asset=r["asset"],
            return_temperature_c=float(r["return_temperature_c"]),
            return_flow_lpm=float(r["return_flow_lpm"]),
            return_conductivity_mscm=float(r["return_conductivity_mscm"]),
            return_pressure_bar=float(r["return_pressure_bar"]),
            explicit_phase=r["phase"] if explicit else None,
        )
        for r in rows
    ]


def reconstruct_one(points: list[SignalPoint]) -> dict:
    result = reconstruct_cycles(points)
    assert result["cycle_count"] == 1
    return result["cycles"][0]


def candidate(tmp_path: Path, scenario: str, *, seed: int, day: int, policy: BehaviorPolicy) -> dict:
    points = scenario_points(tmp_path, scenario, seed=seed, day=day)
    cycle = reconstruct_one(points)
    compliance = evaluate_cycle(cycle, points, recipe())
    return {
        "ingestion_id": f"ing-{day}-{seed}-{scenario}",
        "cycle_id": cycle["cycle_id"],
        "start_ts": cycle["start_ts"],
        "eligible": compliance["overall_assessment"] == "COMPLIANT",
        "eligibility_reason": "eligible" if compliance["overall_assessment"] == "COMPLIANT" else compliance["overall_assessment"],
        "features": extract_behavior_features(cycle, points, profile_bins=policy.profile_bins) if compliance["overall_assessment"] == "COMPLIANT" else None,
        "normalized_sha256": f"norm-{day}-{seed}",
        "reconstruction_sha256": f"recon-{day}-{seed}",
        "compliance_sha256": f"comp-{day}-{seed}",
    }


def normal_baseline(tmp_path: Path, *, count: int = 30) -> dict:
    policy = BehaviorPolicy(minimum_baseline_cycles=20, minimum_feature_cycles=15)
    candidates = [candidate(tmp_path, "normal", seed=100 + i, day=i + 1, policy=policy) for i in range(count)]
    return build_baseline(
        name="normal",
        revision="1",
        asset="HTST-01",
        recipe_name=recipe().name,
        recipe_revision=recipe().revision,
        candidates=candidates,
        policy=policy,
    )


def evaluate_scenario(tmp_path: Path, baseline: dict, scenario: str, *, seed: int = 7, day: int = 60) -> tuple[dict, dict]:
    points = scenario_points(tmp_path, scenario, seed=seed, day=day)
    cycle = reconstruct_one(points)
    compliance = evaluate_cycle(cycle, points, recipe())
    features = extract_behavior_features(cycle, points, profile_bins=baseline["policy"]["profile_bins"])
    return compliance, evaluate_behavior(features, baseline, l2_assessment=compliance["overall_assessment"])


def test_baseline_refuses_too_few_cycles(tmp_path: Path) -> None:
    policy = BehaviorPolicy(minimum_baseline_cycles=20, minimum_feature_cycles=15)
    candidates = [candidate(tmp_path, "normal", seed=100 + i, day=i + 1, policy=policy) for i in range(10)]
    with pytest.raises(ValueError, match="at least 20"):
        build_baseline(
            name="normal", revision="1", asset="HTST-01",
            recipe_name=recipe().name, recipe_revision=recipe().revision,
            candidates=candidates, policy=policy,
        )


def test_normal_cycle_is_behaviorally_normal(tmp_path: Path) -> None:
    baseline = normal_baseline(tmp_path)
    compliance, result = evaluate_scenario(tmp_path, baseline, "normal", seed=999)
    assert compliance["overall_assessment"] == "COMPLIANT"
    assert result["behavioral_assessment"] == "NORMAL"
    assert result["deviation_count"] == 0


def test_compliant_but_low_flow_cycle_is_flagged_without_becoming_l2_failure(tmp_path: Path) -> None:
    baseline = normal_baseline(tmp_path)
    compliance, result = evaluate_scenario(tmp_path, baseline, "compliant_low_flow")
    assert compliance["overall_assessment"] == "COMPLIANT"
    assert result["behavioral_assessment"] == "HIGHLY_UNUSUAL"
    assert any("CAUSTIC.return_flow_lpm" in x["feature"] for x in result["deviations"])
    assert result["principle"].startswith("L3 detects behavior change")


def test_excessive_rinse_is_compliant_but_behaviorally_unusual(tmp_path: Path) -> None:
    baseline = normal_baseline(tmp_path)
    compliance, result = evaluate_scenario(tmp_path, baseline, "excessive_rinse")
    assert compliance["overall_assessment"] == "COMPLIANT"
    assert result["behavioral_assessment"] == "HIGHLY_UNUSUAL"
    assert any(x["feature"] == "phase.FINAL_RINSE.duration_seconds" for x in result["deviations"])


def test_profile_engine_catches_sustained_shape_change_even_when_phase_median_is_near_normal(tmp_path: Path) -> None:
    baseline = normal_baseline(tmp_path)
    compliance, result = evaluate_scenario(tmp_path, baseline, "profile_shift")
    assert compliance["overall_assessment"] == "COMPLIANT"
    assert result["behavioral_assessment"] == "HIGHLY_UNUSUAL"
    profiles = [x for x in result["profile_deviations"] if x["profile"] == "phase.CAUSTIC.return_flow_lpm"]
    assert profiles
    assert profiles[0]["longest_adjacent_run"] >= 2


def test_l2_process_deviation_remains_authoritative(tmp_path: Path) -> None:
    baseline = normal_baseline(tmp_path)
    compliance, result = evaluate_scenario(tmp_path, baseline, "low_temp")
    assert compliance["overall_assessment"] == "PROCESS_DEVIATION"
    assert result["l2_assessment"] == "PROCESS_DEVIATION"
    assert "remains authoritative" in result["l2_authority_note"]


def test_data_review_blocks_behavioral_claims(tmp_path: Path) -> None:
    baseline = normal_baseline(tmp_path)
    compliance, result = evaluate_scenario(tmp_path, baseline, "sensor_freeze")
    assert compliance["overall_assessment"] == "DATA_REVIEW_REQUIRED"
    assert result["behavioral_assessment"] == "NOT_EVALUABLE"
    assert result["deviations"] == []


def test_gross_compliant_outliers_are_screened_from_baseline_training(tmp_path: Path) -> None:
    policy = BehaviorPolicy(minimum_baseline_cycles=20, minimum_feature_cycles=15)
    candidates = [candidate(tmp_path, "normal", seed=200 + i, day=i + 1, policy=policy) for i in range(25)]
    candidates.extend([
        candidate(tmp_path, "excessive_rinse", seed=900, day=40, policy=policy),
        candidate(tmp_path, "excessive_rinse", seed=901, day=41, policy=policy),
    ])
    baseline = build_baseline(
        name="screened", revision="1", asset="HTST-01",
        recipe_name=recipe().name, recipe_revision=recipe().revision,
        candidates=candidates, policy=policy,
    )
    assert baseline["training_cycle_count"] == 25
    assert baseline["excluded_cycle_count"] == 2
    assert all("gross behavioral outlier" in x["reason"] for x in baseline["excluded_cycles"])


def test_noncompliant_cycles_never_train_normal_baseline(tmp_path: Path) -> None:
    policy = BehaviorPolicy(minimum_baseline_cycles=20, minimum_feature_cycles=15)
    candidates = [candidate(tmp_path, "normal", seed=300 + i, day=i + 1, policy=policy) for i in range(20)]
    bad = candidate(tmp_path, "low_temp", seed=999, day=40, policy=policy)
    assert bad["eligible"] is False
    candidates.append(bad)
    baseline = build_baseline(
        name="no-bad-training", revision="1", asset="HTST-01",
        recipe_name=recipe().name, recipe_revision=recipe().revision,
        candidates=candidates, policy=policy,
    )
    assert baseline["training_cycle_count"] == 20
    assert any(x["cycle_id"] == bad["cycle_id"] for x in baseline["excluded_cycles"])


def test_behavior_baseline_revision_is_immutable(tmp_path: Path) -> None:
    baseline = normal_baseline(tmp_path)
    store = BehaviorBaselineStore(tmp_path / "behavior-baselines")
    first = store.save(baseline)
    duplicate = store.save(baseline)
    assert first["saved"] is True
    assert duplicate["duplicate"] is True
    changed = dict(baseline)
    changed["description"] = "mutated"
    with pytest.raises(ValueError, match="immutable"):
        store.save(changed)


def test_baseline_is_asset_and_recipe_specific(tmp_path: Path) -> None:
    baseline = normal_baseline(tmp_path)
    _, result = evaluate_scenario(tmp_path, baseline, "normal")
    assert result["baseline"]["training_cycle_count"] == 30
    assert baseline["asset"] == "HTST-01"
    assert baseline["recipe"]["revision"] == "7"


def test_baseline_member_is_not_scored_against_itself(tmp_path: Path) -> None:
    policy = BehaviorPolicy(minimum_baseline_cycles=20, minimum_feature_cycles=15)
    candidates = [candidate(tmp_path, "normal", seed=500 + i, day=i + 1, policy=policy) for i in range(20)]
    baseline = build_baseline(
        name="leakage-guard", revision="1", asset="HTST-01",
        recipe_name=recipe().name, recipe_revision=recipe().revision,
        candidates=candidates, policy=policy,
    )
    member = candidates[0]
    result = evaluate_behavior(member["features"], baseline, l2_assessment="COMPLIANT")
    assert result["behavioral_assessment"] == "NOT_EVALUABLE"
    assert "self-comparison" in result["conclusion"]


def test_historical_scoring_blocks_lookahead_bias(tmp_path: Path) -> None:
    baseline = normal_baseline(tmp_path)
    points = scenario_points(tmp_path, "normal", seed=777, day=15)
    points = [replace(p, ts=p.ts + timedelta(hours=1)) for p in points]
    cycle = reconstruct_one(points)
    compliance = evaluate_cycle(cycle, points, recipe())
    features = extract_behavior_features(cycle, points, profile_bins=baseline["policy"]["profile_bins"])
    result = evaluate_behavior(features, baseline, l2_assessment=compliance["overall_assessment"])
    assert result["behavioral_assessment"] == "NOT_EVALUABLE"
    assert "look-ahead bias" in result["conclusion"]


def test_api_exposes_behavior_demo() -> None:
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    response = client.get("/v1/demo/behavior/compliant_low_flow")
    assert response.status_code == 200
    body = response.json()
    assert body["compliance"] == "COMPLIANT"
    assert body["behavior"]["behavioral_assessment"] == "HIGHLY_UNUSUAL"
    assert body["baseline_summary"]["training_cycle_count"] >= 20
