from __future__ import annotations

import csv
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.compliance.engine import evaluate_cycle
from app.compliance.models import ValidatedRecipe
from app.economics.engine import build_resource_baseline, calculate_resources, evaluate_economics
from app.economics.models import CostProfile, ResourcePolicy
from app.main import app
from app.reconstruction.engine import reconstruct_cycles
from app.reconstruction.models import SignalPoint
from app.simulator import generate_cycle


RESOURCE_COLUMNS = {
    "fresh_water_flow_lpm": "cip.utility.fresh_water.flow",
    "wastewater_flow_lpm": "cip.utility.wastewater.flow",
    "electric_power_kw": "cip.utility.electric.power",
    "thermal_power_kw": "cip.utility.thermal.power",
    "caustic_dose_kg_min": "cip.chemical.caustic.mass_flow",
    "acid_dose_kg_min": "cip.chemical.acid.mass_flow",
}


def load_cycle(path: Path) -> tuple[list[SignalPoint], list[dict], dict]:
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    points = [
        SignalPoint(
            ts=__import__("datetime").datetime.fromisoformat(r["timestamp"]), asset=r["asset"], explicit_phase=r["phase"],
            return_temperature_c=float(r["return_temperature_c"]), return_flow_lpm=float(r["return_flow_lpm"]),
            return_conductivity_mscm=float(r["return_conductivity_mscm"]), return_pressure_bar=float(r["return_pressure_bar"]),
        ) for r in rows
    ]
    records = []
    for r in rows:
        for column, concept in RESOURCE_COLUMNS.items():
            records.append({"ts_utc": r["timestamp"], "asset": r["asset"], "concept": concept, "value_double": float(r[column]), "quality_code": "GOOD"})
    cycle = reconstruct_cycles(points)["cycles"][0]
    return points, records, cycle


def baseline_for(tmp_path: Path, count: int = 20):
    repo_root = Path(__file__).resolve().parents[3]
    recipe = ValidatedRecipe.model_validate_json((repo_root / "config" / "example_htst_validated_recipe_v7.json").read_text())
    policy = ResourcePolicy(minimum_baseline_cycles=20, minimum_reference_cycles=15)
    candidates = []
    for i in range(count):
        path = generate_cycle(tmp_path / f"base-{i}.csv", scenario="normal", seed=500+i, start=recipe.effective_from + timedelta(days=i+1))
        pts, records, cycle = load_cycle(path)
        comp = evaluate_cycle(cycle, pts, recipe)
        candidates.append({"ingestion_id": f"base-{i}", "cycle_id": cycle["cycle_id"], "start_ts": cycle["start_ts"],
                           "eligible": comp["overall_assessment"] == "COMPLIANT", "summary": calculate_resources(cycle, records, policy)})
    return build_resource_baseline(name="resource-ref", revision="1", asset="HTST-01", recipe_name=recipe.name,
                                   recipe_revision=recipe.revision, candidates=candidates, policy=policy), recipe, policy


def test_return_circulation_flow_is_never_counted_as_water(tmp_path: Path) -> None:
    path = generate_cycle(tmp_path / "normal.csv")
    _, _, cycle = load_cycle(path)
    # This is deliberately only the process return flow, not a utility meter.
    fake = [{"ts_utc": cycle["start_ts"], "concept": "cip.return.flow", "value_double": 420.0, "quality_code": "GOOD"}]
    result = calculate_resources(cycle, fake)
    assert result["resources"]["water_m3"]["status"] == "NOT_EVALUABLE"
    assert result["resources"]["water_m3"]["quantity"] is None


def test_measured_utility_signals_integrate_resources(tmp_path: Path) -> None:
    path = generate_cycle(tmp_path / "normal.csv")
    _, records, cycle = load_cycle(path)
    result = calculate_resources(cycle, records)
    assert result["resources"]["water_m3"]["status"] == "MEASURED"
    assert 9.0 < result["resources"]["water_m3"]["quantity"] < 11.0
    assert result["resources"]["electricity_kwh"]["quantity"] > 7.0
    assert result["resources"]["thermal_energy_kwh"]["quantity"] > 60.0
    assert result["resources"]["caustic_kg"]["quantity"] > 10.0


def test_missing_meter_interval_makes_quantity_not_evaluable(tmp_path: Path) -> None:
    path = generate_cycle(tmp_path / "normal.csv")
    _, records, cycle = load_cycle(path)
    water = [r for r in records if r["concept"] != "cip.utility.fresh_water.flow"]
    original_water = [r for r in records if r["concept"] == "cip.utility.fresh_water.flow"]
    # Keep only the first and last minute, leaving a giant unobserved interval.
    water += original_water[:6] + original_water[-6:]
    result = calculate_resources(cycle, water)
    assert result["resources"]["water_m3"]["status"] == "NOT_EVALUABLE"
    assert result["resources"]["water_m3"]["observed_partial_quantity"] >= 0


def test_cost_profile_has_no_fake_default_rates(tmp_path: Path) -> None:
    path = generate_cycle(tmp_path / "normal.csv")
    _, records, cycle = load_cycle(path)
    summary = calculate_resources(cycle, records)
    result = evaluate_economics(summary, None, CostProfile(name="plant", revision="1"), l2_assessment="COMPLIANT")
    assert result["actual_resource_cost"] is None
    assert result["actual_cost_items"] == []


def test_resource_baseline_refuses_too_few_cycles(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 20"):
        baseline_for(tmp_path, count=19)


def test_excessive_rinse_becomes_resource_and_capacity_candidate(tmp_path: Path) -> None:
    baseline, recipe, policy = baseline_for(tmp_path)
    path = generate_cycle(tmp_path / "excess.csv", scenario="excessive_rinse", seed=7, start=recipe.effective_from + timedelta(days=60))
    _, records, cycle = load_cycle(path)
    summary = calculate_resources(cycle, records, policy)
    costs = CostProfile(name="demo", revision="1", water_cost_per_m3=1.2, wastewater_cost_per_m3=1.0,
                        electricity_cost_per_kwh=.1, thermal_energy_cost_per_kwh=.05, caustic_cost_per_kg=1,
                        acid_cost_per_kg=.8, incremental_production_value_per_hour=800, annual_cycles=300)
    result = evaluate_economics(summary, baseline, costs, l2_assessment="COMPLIANT")
    resources = {x.get("resource") for x in result["optimization_candidates"]}
    assert "water_m3" in resources
    assert "wastewater_m3" in resources
    time = next(x for x in result["optimization_candidates"] if x["type"] == "EXCESS_CIP_TIME_VS_HISTORICAL_MEDIAN")
    assert 6.5/60 < time["recoverable_capacity_hours_candidate"] < 7.5/60
    assert result["per_cycle_opportunity"] > 90
    assert result["annualized_opportunity_scenario"] > 27000


def test_normal_cycle_does_not_create_material_opportunity(tmp_path: Path) -> None:
    baseline, recipe, policy = baseline_for(tmp_path)
    path = generate_cycle(tmp_path / "current.csv", scenario="normal", seed=901, start=recipe.effective_from + timedelta(days=60))
    _, records, cycle = load_cycle(path)
    summary = calculate_resources(cycle, records, policy)
    result = evaluate_economics(summary, baseline, CostProfile(name="demo", revision="1", water_cost_per_m3=1.2), l2_assessment="COMPLIANT")
    assert result["per_cycle_opportunity"] == 0


def test_capacity_value_is_not_applied_to_entire_necessary_cip(tmp_path: Path) -> None:
    baseline, recipe, policy = baseline_for(tmp_path)
    path = generate_cycle(tmp_path / "normal2.csv", scenario="normal", seed=800, start=recipe.effective_from + timedelta(days=60))
    _, records, cycle = load_cycle(path)
    result = evaluate_economics(calculate_resources(cycle, records, policy), baseline,
                                CostProfile(name="x", revision="1", incremental_production_value_per_hour=10000), l2_assessment="COMPLIANT")
    assert not any(x["type"] == "EXCESS_CIP_TIME_VS_HISTORICAL_MEDIAN" for x in result["optimization_candidates"])


def test_annualization_requires_explicit_cycle_frequency(tmp_path: Path) -> None:
    baseline, recipe, policy = baseline_for(tmp_path)
    path = generate_cycle(tmp_path / "excess2.csv", scenario="excessive_rinse", start=recipe.effective_from + timedelta(days=60))
    _, records, cycle = load_cycle(path)
    result = evaluate_economics(calculate_resources(cycle, records, policy), baseline,
                                CostProfile(name="x", revision="1", water_cost_per_m3=1.0), l2_assessment="COMPLIANT")
    assert result["per_cycle_opportunity"] > 0
    assert result["annualized_opportunity_scenario"] is None


def test_cycle_cannot_be_scored_against_baseline_containing_it(tmp_path: Path) -> None:
    baseline, recipe, policy = baseline_for(tmp_path)
    first = baseline["training_lineage"][0]
    # Construct enough of the summary to trigger the anti-self-comparison guard first.
    summary = {"cycle_id": first["cycle_id"], "asset": "HTST-01", "start_ts": first["start_ts"], "duration_seconds": 1, "resources": {}}
    with pytest.raises(ValueError, match="containing itself"):
        evaluate_economics(summary, baseline, CostProfile(name="x", revision="1"), l2_assessment="COMPLIANT")


def test_api_exposes_resource_economics_demo() -> None:
    client = TestClient(app)
    response = client.get("/v1/demo/economics/excessive_rinse")
    assert response.status_code == 200
    body = response.json()
    assert body["economics"]["per_cycle_opportunity"] > 0
    assert body["resource_summary"]["resources"]["water_m3"]["status"] == "MEASURED"


def test_noncompliant_cycle_keeps_accounting_but_blocks_reduction_candidates(tmp_path: Path) -> None:
    baseline, recipe, policy = baseline_for(tmp_path)
    path = generate_cycle(tmp_path / "failed-excess.csv", scenario="excessive_rinse", seed=77, start=recipe.effective_from + timedelta(days=60))
    _, records, cycle = load_cycle(path)
    result = evaluate_economics(calculate_resources(cycle, records, policy), baseline,
                                CostProfile(name="x", revision="1", water_cost_per_m3=1.0, incremental_production_value_per_hour=1000),
                                l2_assessment="PROCESS_DEVIATION")
    assert result["actual_resource_cost"] is not None
    assert result["optimization_candidates"] == []
    assert "withheld" in result["optimization_blocked_reason"]
