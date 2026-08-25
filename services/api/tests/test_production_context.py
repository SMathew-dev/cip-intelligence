from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.behavior.features import extract_behavior_features
from app.compliance.engine import evaluate_cycle
from app.compliance.models import ValidatedRecipe
from app.production_context.engine import build_context_baseline, build_production_context, evaluate_context
from app.production_context.models import ContextPolicy, ProductionRun, ProductionRunMetrics
from app.production_context.store import ContextBaselineStore, ProductionRunStore
from app.reconstruction.engine import reconstruct_cycles
from app.reconstruction.models import SignalPoint
from app.simulator import generate_cycle

REPO_ROOT = Path(__file__).resolve().parents[3]


def recipe() -> ValidatedRecipe:
    return ValidatedRecipe.model_validate_json(
        (REPO_ROOT / "config" / "example_htst_validated_recipe_v7.json").read_text(encoding="utf-8")
    )


def cip(tmp_path: Path, scenario: str, *, seed: int, start: datetime) -> tuple[list[SignalPoint], dict, dict, dict]:
    path = tmp_path / f"{scenario}-{seed}.csv"
    generate_cycle(path, scenario=scenario, seed=seed, start=start)
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    points = [SignalPoint(
        ts=datetime.fromisoformat(r["timestamp"]), asset=r["asset"],
        return_temperature_c=float(r["return_temperature_c"]), return_flow_lpm=float(r["return_flow_lpm"]),
        return_conductivity_mscm=float(r["return_conductivity_mscm"]), return_pressure_bar=float(r["return_pressure_bar"]),
        explicit_phase=r["phase"],
    ) for r in rows]
    result = reconstruct_cycles(points)
    assert result["cycle_count"] == 1
    cycle = result["cycles"][0]
    compliance = evaluate_cycle(cycle, points, recipe())
    features = extract_behavior_features(cycle, points, profile_bins=8)
    return points, cycle, compliance, features


def production_run(run_id: str, cip_start: datetime, *, hours: float, long_context: bool = False,
                   product: str = "MILK-A", family: str = "MILK", asset: str = "HTST-01",
                   end_offset_minutes: float = 15) -> ProductionRun:
    end = cip_start - timedelta(minutes=end_offset_minutes)
    start = end - timedelta(hours=hours)
    return ProductionRun(
        run_id=run_id, asset=asset, product_code=product, product_family=family,
        batch_ref=f"B-{run_id}", start_ts=start, end_ts=end,
        source_type="SIMULATOR", source_ref=f"sim://{run_id}",
        metrics=ProductionRunMetrics(
            average_throughput_lph=10000 if not long_context else 10500,
            fat_pct=3.25, protein_pct=3.15, total_solids_pct=12.4,
            process_temperature_avg_c=74.2, process_temperature_max_c=75.0,
            shutdown_minutes=2 if not long_context else 14,
            pressure_drop_start_bar=0.50,
            pressure_drop_end_bar=0.62 if not long_context else 0.94,
            normalized_heat_transfer_start=1.0,
            normalized_heat_transfer_end=0.97 if not long_context else 0.86,
        ),
    )


def candidate(tmp_path: Path, *, day: int, long_context: bool) -> dict:
    start = recipe().effective_from + timedelta(days=day)
    scenario = "context_long_run_response" if long_context else "normal"
    _, cycle, compliance, features = cip(tmp_path, scenario, seed=1000 + day, start=start)
    run = production_run(f"run-{day}", start, hours=12 if long_context else 6, long_context=long_context)
    context = build_production_context(cycle, [run], policy=ContextPolicy())
    assert compliance["overall_assessment"] == "COMPLIANT"
    return {
        "ingestion_id": f"ing-{day}", "cycle_id": cycle["cycle_id"], "start_ts": cycle["start_ts"],
        "eligible": True, "context": context, "cip_features": features,
    }


def baseline(tmp_path: Path) -> dict:
    policy = ContextPolicy(
        minimum_training_cycles=20, minimum_comparable_cycles=8, maximum_neighbors=10,
        minimum_shared_context_features=3, max_context_distance=2.5,
    )
    candidates = [candidate(tmp_path, day=i + 1, long_context=False) for i in range(10)]
    candidates += [candidate(tmp_path, day=i + 20, long_context=True) for i in range(10)]
    return build_context_baseline(
        name="production-context", revision="1", asset="HTST-01", recipe_name=recipe().name,
        recipe_revision=recipe().revision, candidates=candidates, policy=policy,
    )


def test_context_links_preceding_run_and_derives_features(tmp_path: Path) -> None:
    start = recipe().effective_from + timedelta(days=60)
    _, cycle, _, _ = cip(tmp_path, "normal", seed=4, start=start)
    run = production_run("r1", start, hours=6)
    context = build_production_context(cycle, [run])
    assert context["context_status"] == "AVAILABLE"
    assert context["campaign"]["run_ids"] == ["r1"]
    assert context["context_features"]["production.total_duration_hours"]["value"] == pytest.approx(6)
    assert context["context_features"]["production.total_volume_l"]["value"] == pytest.approx(60000)
    assert context["context_features"]["production.pressure_drop_change_bar"]["value"] == pytest.approx(0.12)
    assert "soil" not in " ".join(context["context_features"].keys()).lower()


def test_multiple_contiguous_runs_form_one_uncleaned_campaign(tmp_path: Path) -> None:
    start = recipe().effective_from + timedelta(days=61)
    _, cycle, _, _ = cip(tmp_path, "normal", seed=5, start=start)
    r2 = production_run("r2", start, hours=3, product="MILK-B")
    # r1 ends 30 minutes before r2 starts: inside the default inter-run campaign gap.
    r1_end = r2.start_ts - timedelta(minutes=30)
    r1 = ProductionRun(
        run_id="r1", asset="HTST-01", product_code="MILK-A", product_family="MILK",
        start_ts=r1_end - timedelta(hours=3), end_ts=r1_end, source_type="SIMULATOR", source_ref="sim://r1",
        metrics=ProductionRunMetrics(average_throughput_lph=9000),
    )
    context = build_production_context(cycle, [r1, r2])
    assert context["campaign"]["run_ids"] == ["r1", "r2"]
    assert context["context_features"]["production.product_change_count"]["value"] == 1
    assert context["context_features"]["production.internal_idle_hours"]["value"] == pytest.approx(0.5)


def test_large_gap_starts_new_campaign(tmp_path: Path) -> None:
    start = recipe().effective_from + timedelta(days=62)
    _, cycle, _, _ = cip(tmp_path, "normal", seed=6, start=start)
    recent = production_run("recent", start, hours=4)
    old_end = recent.start_ts - timedelta(hours=8)
    old = ProductionRun(
        run_id="old", asset="HTST-01", product_code="MILK-A", product_family="MILK",
        start_ts=old_end - timedelta(hours=4), end_ts=old_end, source_type="SIMULATOR", source_ref="sim://old",
    )
    context = build_production_context(cycle, [old, recent])
    assert context["campaign"]["run_ids"] == ["recent"]


def test_production_overlap_with_cip_is_a_conflict(tmp_path: Path) -> None:
    start = recipe().effective_from + timedelta(days=63)
    _, cycle, _, _ = cip(tmp_path, "normal", seed=7, start=start)
    run = ProductionRun(
        run_id="overlap", asset="HTST-01", product_code="MILK-A", start_ts=start - timedelta(hours=1),
        end_ts=start + timedelta(minutes=10), source_type="SIMULATOR", source_ref="sim://overlap",
    )
    context = build_production_context(cycle, [run])
    assert context["context_status"] == "CONFLICT"
    assert context["issues"][0]["code"] == "PRODUCTION_OVERLAPS_CIP"


def test_missing_volume_is_not_fabricated(tmp_path: Path) -> None:
    start = recipe().effective_from + timedelta(days=64)
    _, cycle, _, _ = cip(tmp_path, "normal", seed=8, start=start)
    run = ProductionRun(
        run_id="no-volume", asset="HTST-01", product_code="MILK-A", start_ts=start - timedelta(hours=6),
        end_ts=start - timedelta(minutes=15), source_type="SIMULATOR", source_ref="sim://no-volume",
    )
    context = build_production_context(cycle, [run])
    assert "production.total_volume_l" not in context["context_features"]
    assert any(x["code"] == "PARTIAL_PRODUCTION_VOLUME_EVIDENCE" for x in context["issues"])


def test_long_idle_is_context_not_automatic_failure(tmp_path: Path) -> None:
    start = recipe().effective_from + timedelta(days=65)
    _, cycle, _, _ = cip(tmp_path, "normal", seed=9, start=start)
    run = production_run("idle", start, hours=6, end_offset_minutes=180)
    context = build_production_context(cycle, [run])
    assert context["context_status"] == "AVAILABLE"
    assert any(x["code"] == "LONG_PRE_CIP_IDLE" for x in context["issues"])


def test_long_run_response_is_typical_among_similar_long_run_contexts(tmp_path: Path) -> None:
    b = baseline(tmp_path)
    start = recipe().effective_from + timedelta(days=80)
    _, cycle, compliance, features = cip(tmp_path, "context_long_run_response", seed=999, start=start)
    context = build_production_context(cycle, [production_run("current-long", start, hours=12, long_context=True)])
    result = evaluate_context(context, features, b, l2_assessment=compliance["overall_assessment"])
    assert result["context_assessment"] == "CONTEXTUALLY_TYPICAL"
    assert result["comparable_cycle_count"] >= 8


def test_same_long_cleaning_after_short_run_is_contextually_unusual(tmp_path: Path) -> None:
    b = baseline(tmp_path)
    start = recipe().effective_from + timedelta(days=81)
    _, cycle, compliance, features = cip(tmp_path, "context_long_run_response", seed=998, start=start)
    context = build_production_context(cycle, [production_run("current-short", start, hours=6, long_context=False)])
    result = evaluate_context(context, features, b, l2_assessment=compliance["overall_assessment"])
    assert result["context_assessment"] == "CONTEXTUALLY_UNUSUAL"
    assert any(x["feature"] == "phase.FINAL_RINSE.duration_seconds" for x in result["behavior_differences"])


def test_different_product_family_does_not_get_forced_comparison(tmp_path: Path) -> None:
    b = baseline(tmp_path)
    start = recipe().effective_from + timedelta(days=82)
    _, cycle, compliance, features = cip(tmp_path, "normal", seed=997, start=start)
    run = production_run("cream", start, hours=6, product="CREAM-A", family="CREAM")
    context = build_production_context(cycle, [run])
    result = evaluate_context(context, features, b, l2_assessment=compliance["overall_assessment"])
    assert result["context_assessment"] == "INSUFFICIENT_COMPARABLES"


def test_data_review_blocks_contextual_behavior_claim(tmp_path: Path) -> None:
    b = baseline(tmp_path)
    start = recipe().effective_from + timedelta(days=83)
    _, cycle, compliance, features = cip(tmp_path, "sensor_freeze", seed=996, start=start)
    assert compliance["overall_assessment"] == "DATA_REVIEW_REQUIRED"
    context = build_production_context(cycle, [production_run("freeze", start, hours=6)])
    result = evaluate_context(context, features, b, l2_assessment=compliance["overall_assessment"])
    assert result["context_assessment"] == "NOT_EVALUABLE"


def test_context_baseline_member_cannot_score_itself(tmp_path: Path) -> None:
    policy = ContextPolicy(minimum_training_cycles=20, minimum_comparable_cycles=8)
    cases = [candidate(tmp_path, day=i + 1, long_context=False) for i in range(20)]
    b = build_context_baseline(
        name="self", revision="1", asset="HTST-01", recipe_name=recipe().name,
        recipe_revision=recipe().revision, candidates=cases, policy=policy,
    )
    member = cases[0]
    result = evaluate_context(member["context"], member["cip_features"], b, l2_assessment="COMPLIANT")
    assert result["context_assessment"] == "NOT_EVALUABLE"
    assert "self-comparison" in result["conclusion"]


def test_context_baseline_store_is_immutable(tmp_path: Path) -> None:
    b = baseline(tmp_path)
    store = ContextBaselineStore(tmp_path / "baselines")
    first = store.save(b)
    dup = store.save(b)
    assert first["saved"] is True and dup["duplicate"] is True
    changed = dict(b)
    changed["description"] = "mutated"
    with pytest.raises(ValueError, match="immutable"):
        store.save(changed)


def test_production_run_store_is_idempotent_and_immutable(tmp_path: Path) -> None:
    start = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    run = production_run("stable-id", start, hours=5)
    store = ProductionRunStore(tmp_path / "runs")
    assert store.save(run)["saved"] is True
    assert store.save(run)["duplicate"] is True
    changed = run.model_copy(deep=True)
    changed.metrics.fat_pct = 4.0
    with pytest.raises(ValueError, match="immutable"):
        store.save(changed)


def test_context_scoring_blocks_lookahead_bias(tmp_path: Path) -> None:
    b = baseline(tmp_path)
    # Baseline includes cycles through roughly day 29. Score a distinct cycle from day 15.
    start = recipe().effective_from + timedelta(days=15, hours=1)
    _, cycle, compliance, features = cip(tmp_path, "normal", seed=5511, start=start)
    context = build_production_context(cycle, [production_run("historical-current", start, hours=6)])
    result = evaluate_context(context, features, b, l2_assessment=compliance["overall_assessment"])
    assert result["context_assessment"] == "NOT_EVALUABLE"
    assert "look-ahead" in result["conclusion"]
