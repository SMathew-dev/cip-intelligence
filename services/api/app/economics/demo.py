from __future__ import annotations

import csv
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.compliance.engine import evaluate_cycle
from app.compliance.models import ValidatedRecipe
from app.reconstruction.engine import reconstruct_cycles
from app.reconstruction.models import SignalPoint
from app.simulator import generate_cycle

from .engine import build_resource_baseline, calculate_resources, evaluate_economics
from .models import CostProfile, ResourcePolicy


def _load(path: Path) -> tuple[list[SignalPoint], list[dict]]:
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    points = [
        SignalPoint(
            ts=__import__("datetime").datetime.fromisoformat(r["timestamp"]), asset=r["asset"],
            return_temperature_c=float(r["return_temperature_c"]), return_flow_lpm=float(r["return_flow_lpm"]),
            return_conductivity_mscm=float(r["return_conductivity_mscm"]), return_pressure_bar=float(r["return_pressure_bar"]),
            explicit_phase=r["phase"],
        ) for r in rows
    ]
    concepts = {
        "fresh_water_flow_lpm": "cip.utility.fresh_water.flow",
        "wastewater_flow_lpm": "cip.utility.wastewater.flow",
        "electric_power_kw": "cip.utility.electric.power",
        "thermal_power_kw": "cip.utility.thermal.power",
        "caustic_dose_kg_min": "cip.chemical.caustic.mass_flow",
        "acid_dose_kg_min": "cip.chemical.acid.mass_flow",
    }
    records = []
    for r in rows:
        for column, concept in concepts.items():
            records.append({"ts_utc": r["timestamp"], "asset": r["asset"], "concept": concept, "value_double": float(r[column]), "quality_code": "GOOD"})
    return points, records


def demo_economics(repo_root: Path, scenario: str) -> dict:
    recipe = ValidatedRecipe.model_validate_json((repo_root / "config" / "example_htst_validated_recipe_v7.json").read_text())
    policy = ResourcePolicy()
    candidates = []
    for i in range(30):
        with NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            path = Path(tmp.name)
        generate_cycle(path, scenario="normal", seed=300 + i, start=recipe.effective_from + timedelta(days=i + 1))
        pts, records = _load(path)
        cycle = reconstruct_cycles(pts)["cycles"][0]
        comp = evaluate_cycle(cycle, pts, recipe)
        summary = calculate_resources(cycle, records, policy)
        candidates.append({"ingestion_id": f"econ-demo-{i}", "cycle_id": cycle["cycle_id"], "start_ts": cycle["start_ts"], "eligible": comp["overall_assessment"] == "COMPLIANT", "summary": summary})
    baseline = build_resource_baseline(name="HTST-01-resource-reference", revision="demo-1", asset="HTST-01", recipe_name=recipe.name,
                                       recipe_revision=recipe.revision, candidates=candidates, policy=policy,
                                       description="Simulator-only historical resource reference; values are not industry benchmarks.")
    with NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        path = Path(tmp.name)
    generate_cycle(path, scenario=scenario, seed=7, start=recipe.effective_from + timedelta(days=60))
    pts, records = _load(path)
    cycle = reconstruct_cycles(pts)["cycles"][0]
    current_compliance = evaluate_cycle(cycle, pts, recipe)
    summary = calculate_resources(cycle, records, policy)
    costs = CostProfile(name="DEMO COSTS - NOT PLANT RATES", revision="demo-1", currency="USD", approval_ref="SIMULATOR ONLY",
                        water_cost_per_m3=1.2, wastewater_cost_per_m3=1.0, electricity_cost_per_kwh=0.10,
                        thermal_energy_cost_per_kwh=0.05, caustic_cost_per_kg=1.0, acid_cost_per_kg=0.8,
                        incremental_production_value_per_hour=800.0, annual_cycles=300,
                        notes="Arbitrary development inputs solely to exercise the economics engine.")
    return {"scenario": scenario, "resource_summary": summary, "economics": evaluate_economics(summary, baseline, costs, l2_assessment=current_compliance["overall_assessment"]),
            "reference": {"training_cycle_count": baseline["training_cycle_count"], "name": baseline["name"], "revision": baseline["revision"]}}
