from __future__ import annotations

import csv
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.behavior.engine import build_baseline
from app.behavior.features import extract_behavior_features
from app.behavior.models import BehaviorPolicy
from app.compliance.engine import evaluate_cycle
from app.compliance.models import ValidatedRecipe
from app.economics.engine import build_resource_baseline, calculate_resources, evaluate_economics
from app.economics.models import CostProfile, ResourcePolicy
from app.reconstruction.engine import reconstruct_cycles
from app.reconstruction.models import SignalPoint
from app.simulator import generate_cycle

from .engine import discover_final_rinse_candidate
from .models import OptimizationPolicy, OutcomeHistorySummary


def _load(path: Path) -> tuple[list[SignalPoint], list[dict]]:
    with path.open(encoding="utf-8") as f:
        rows=list(csv.DictReader(f))
    points=[SignalPoint(
        ts=__import__("datetime").datetime.fromisoformat(r["timestamp"]), asset=r["asset"],
        return_temperature_c=float(r["return_temperature_c"]), return_flow_lpm=float(r["return_flow_lpm"]),
        return_conductivity_mscm=float(r["return_conductivity_mscm"]), return_pressure_bar=float(r["return_pressure_bar"]),
        explicit_phase=r["phase"],
    ) for r in rows]
    concepts={
        "fresh_water_flow_lpm":"cip.utility.fresh_water.flow",
        "wastewater_flow_lpm":"cip.utility.wastewater.flow",
        "electric_power_kw":"cip.utility.electric.power",
        "thermal_power_kw":"cip.utility.thermal.power",
        "caustic_dose_kg_min":"cip.chemical.caustic.mass_flow",
        "acid_dose_kg_min":"cip.chemical.acid.mass_flow",
    }
    records=[]
    for r in rows:
        for column,concept in concepts.items():
            records.append({"ts_utc":r["timestamp"],"asset":r["asset"],"concept":concept,"value_double":float(r[column]),"quality_code":"GOOD"})
    return points,records


def demo_optimization(repo_root: Path, scenario: str = "excessive_rinse") -> dict:
    if scenario not in {"normal","excessive_rinse","low_flow","sensor_freeze"}:
        raise ValueError("unsupported optimization demo scenario")
    recipe=ValidatedRecipe.model_validate_json((repo_root/"config"/"example_htst_validated_recipe_v7.json").read_text())
    bpolicy=BehaviorPolicy(); rpolicy=ResourcePolicy()
    bcandidates=[]; rcandidates=[]
    for i in range(35):
        with NamedTemporaryFile(suffix=".csv",delete=False) as t:path=Path(t.name)
        generate_cycle(path,"normal",seed=700+i,start=recipe.effective_from+timedelta(days=i+1))
        pts,recs=_load(path); cycle=reconstruct_cycles(pts)["cycles"][0]; comp=evaluate_cycle(cycle,pts,recipe)
        feat=extract_behavior_features(cycle,pts,profile_bins=bpolicy.profile_bins)
        bcandidates.append({"ingestion_id":f"opt-{i}","cycle_id":cycle["cycle_id"],"start_ts":cycle["start_ts"],"eligible":comp["overall_assessment"]=="COMPLIANT","features":feat})
        rcandidates.append({"ingestion_id":f"opt-{i}","cycle_id":cycle["cycle_id"],"start_ts":cycle["start_ts"],"eligible":comp["overall_assessment"]=="COMPLIANT","summary":calculate_resources(cycle,recs,rpolicy)})
    bbaseline=build_baseline(name="HTST-01-behavior-reference",revision="demo-1",asset="HTST-01",recipe_name=recipe.name,recipe_revision=recipe.revision,candidates=bcandidates,policy=bpolicy,description="Simulator only")
    rbaseline=build_resource_baseline(name="HTST-01-resource-reference",revision="demo-1",asset="HTST-01",recipe_name=recipe.name,recipe_revision=recipe.revision,candidates=rcandidates,policy=rpolicy,description="Simulator only")

    with NamedTemporaryFile(suffix=".csv",delete=False) as t:path=Path(t.name)
    generate_cycle(path,scenario,seed=999,start=recipe.effective_from+timedelta(days=80))
    pts,recs=_load(path); cycle=reconstruct_cycles(pts)["cycles"][0]; comp=evaluate_cycle(cycle,pts,recipe)
    summary=calculate_resources(cycle,recs,rpolicy)
    costs=CostProfile(name="DEMO COSTS - NOT PLANT RATES",revision="demo-1",currency="USD",approval_ref="SIMULATOR ONLY",water_cost_per_m3=1.2,wastewater_cost_per_m3=1.0,electricity_cost_per_kwh=.10,thermal_energy_cost_per_kwh=.05,caustic_cost_per_kg=1.0,acid_cost_per_kg=.8,incremental_production_value_per_hour=800.0,annual_cycles=300,notes="Arbitrary development inputs")
    econ=evaluate_economics(summary,rbaseline,costs,l2_assessment=comp["overall_assessment"])
    outcomes=OutcomeHistorySummary(comparable_cycles=35,cycles_with_verification=32,passed_verifications=32,failed_verifications=0)
    diagnostics={"detections":[],"hypotheses":[],"confirmed_conditions":[],"diagnostic_status":"NO_FINDINGS"}
    candidate=discover_final_rinse_candidate(cycle,pts,recipe,compliance=comp,behavior_baseline=bbaseline,economics=econ,diagnostics=diagnostics,outcome_history=outcomes,policy=OptimizationPolicy())
    return {"scenario":scenario,"l2":comp["overall_assessment"],"candidate":candidate,"economics":econ,"reference":{"behavior_training_cycles":bbaseline["training_cycle_count"],"outcome_cycles":outcomes.model_dump(mode="json")}}
