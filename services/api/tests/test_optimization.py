from __future__ import annotations

import csv
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi.testclient import TestClient

from app.behavior.engine import build_baseline
from app.behavior.features import extract_behavior_features
from app.behavior.models import BehaviorPolicy
from app.compliance.engine import evaluate_cycle
from app.compliance.models import ValidatedRecipe
from app.economics.engine import build_resource_baseline, calculate_resources, evaluate_economics
from app.economics.models import CostProfile, ResourcePolicy
from app.main import app
from app.optimization.engine import assess_controlled_trial, discover_final_rinse_candidate
from app.optimization.models import OptimizationPolicy, OutcomeHistorySummary, TrialCycleResult
from app.reconstruction.engine import reconstruct_cycles
from app.reconstruction.models import SignalPoint
from app.simulator import generate_cycle

ROOT=Path(__file__).resolve().parents[3]
RECIPE=ValidatedRecipe.model_validate_json((ROOT/"config"/"example_htst_validated_recipe_v7.json").read_text())


def load(path:Path):
    with path.open() as f: rows=list(csv.DictReader(f))
    pts=[SignalPoint(ts=__import__("datetime").datetime.fromisoformat(r["timestamp"]),asset=r["asset"],return_temperature_c=float(r["return_temperature_c"]),return_flow_lpm=float(r["return_flow_lpm"]),return_conductivity_mscm=float(r["return_conductivity_mscm"]),return_pressure_bar=float(r["return_pressure_bar"]),explicit_phase=r["phase"]) for r in rows]
    concepts={"fresh_water_flow_lpm":"cip.utility.fresh_water.flow","wastewater_flow_lpm":"cip.utility.wastewater.flow","electric_power_kw":"cip.utility.electric.power","thermal_power_kw":"cip.utility.thermal.power","caustic_dose_kg_min":"cip.chemical.caustic.mass_flow","acid_dose_kg_min":"cip.chemical.acid.mass_flow"}
    records=[]
    for r in rows:
        for col,concept in concepts.items(): records.append({"ts_utc":r["timestamp"],"asset":r["asset"],"concept":concept,"value_double":float(r[col]),"quality_code":"GOOD"})
    return pts,records


def fixtures(tmp_path:Path):
    bp=BehaviorPolicy(); rp=ResourcePolicy(); bc=[]; rc=[]
    for i in range(35):
        p=generate_cycle(tmp_path/f"n{i}.csv","normal",seed=1000+i,start=RECIPE.effective_from+timedelta(days=i+1))
        pts,recs=load(p); cycle=reconstruct_cycles(pts)["cycles"][0]; comp=evaluate_cycle(cycle,pts,RECIPE)
        bc.append({"ingestion_id":f"i{i}","cycle_id":cycle["cycle_id"],"start_ts":cycle["start_ts"],"eligible":comp["overall_assessment"]=="COMPLIANT","features":extract_behavior_features(cycle,pts,profile_bins=bp.profile_bins)})
        rc.append({"ingestion_id":f"i{i}","cycle_id":cycle["cycle_id"],"start_ts":cycle["start_ts"],"eligible":comp["overall_assessment"]=="COMPLIANT","summary":calculate_resources(cycle,recs,rp)})
    bb=build_baseline(name="b",revision="1",asset="HTST-01",recipe_name=RECIPE.name,recipe_revision=RECIPE.revision,candidates=bc,policy=bp)
    rb=build_resource_baseline(name="r",revision="1",asset="HTST-01",recipe_name=RECIPE.name,recipe_revision=RECIPE.revision,candidates=rc,policy=rp)
    costs=CostProfile(name="demo",revision="1",water_cost_per_m3=1,wastewater_cost_per_m3=1,electricity_cost_per_kwh=.1,incremental_production_value_per_hour=500,annual_cycles=300)
    return bb,rb,costs,rp


def current(tmp_path:Path, scenario:str, rb,costs,rp):
    p=generate_cycle(tmp_path/f"{scenario}.csv",scenario,seed=2001,start=RECIPE.effective_from+timedelta(days=80))
    pts,recs=load(p); cycle=reconstruct_cycles(pts)["cycles"][0]; comp=evaluate_cycle(cycle,pts,RECIPE)
    econ=evaluate_economics(calculate_resources(cycle,recs,rp),rb,costs,l2_assessment=comp["overall_assessment"])
    return cycle,pts,comp,econ


def outcomes(): return OutcomeHistorySummary(comparable_cycles=35,cycles_with_verification=32,passed_verifications=32,failed_verifications=0)

def clean_dx(): return {"detections":[],"hypotheses":[],"confirmed_conditions":[],"diagnostic_status":"NO_FINDINGS"}


def test_excessive_rinse_becomes_controlled_validation_candidate(tmp_path):
    bb,rb,costs,rp=fixtures(tmp_path); c,p,comp,econ=current(tmp_path,"excessive_rinse",rb,costs,rp)
    out=discover_final_rinse_candidate(c,p,RECIPE,compliance=comp,behavior_baseline=bb,economics=econ,diagnostics=clean_dx(),outcome_history=outcomes())
    assert out["eligibility"]=="ELIGIBLE_FOR_CONTROLLED_VALIDATION"
    assert out["proposed_controlled_trial"]["automatic_control_change"] is False
    assert out["proposed_controlled_trial"]["nominal_review_target_seconds"] < out["current_final_rinse_seconds"]
    assert out["observed_endpoint"]["approved_endpoint_condition_remains_authoritative"] is True


def test_normal_rinse_does_not_manufacture_optimization(tmp_path):
    bb,rb,costs,rp=fixtures(tmp_path); c,p,comp,econ=current(tmp_path,"normal",rb,costs,rp)
    out=discover_final_rinse_candidate(c,p,RECIPE,compliance=comp,behavior_baseline=bb,economics=econ,diagnostics=clean_dx(),outcome_history=outcomes())
    assert out["eligibility"]=="BLOCKED"
    assert any("no defensible shorter" in x or "below optimization policy minimum" in x for x in out["blockers"])


def test_noncompliant_cycle_is_blocked(tmp_path):
    bb,rb,costs,rp=fixtures(tmp_path); c,p,comp,econ=current(tmp_path,"low_flow",rb,costs,rp)
    out=discover_final_rinse_candidate(c,p,RECIPE,compliance=comp,behavior_baseline=bb,economics=econ,diagnostics=clean_dx(),outcome_history=outcomes())
    assert out["eligibility"]=="BLOCKED"
    assert any("L2 assessment" in x for x in out["blockers"])


def test_missing_qa_evidence_blocks_hygiene_sensitive_change(tmp_path):
    bb,rb,costs,rp=fixtures(tmp_path); c,p,comp,econ=current(tmp_path,"excessive_rinse",rb,costs,rp)
    out=discover_final_rinse_candidate(c,p,RECIPE,compliance=comp,behavior_baseline=bb,economics=econ,diagnostics=clean_dx(),outcome_history=None)
    assert out["eligibility"]=="BLOCKED"
    assert any("QA" in x for x in out["blockers"])


def test_unresolved_high_diagnostic_blocks_trial(tmp_path):
    bb,rb,costs,rp=fixtures(tmp_path); c,p,comp,econ=current(tmp_path,"excessive_rinse",rb,costs,rp)
    dx={"detections":[],"hypotheses":[{"code":"DX-X","severity":"HIGH"}],"confirmed_conditions":[]}
    out=discover_final_rinse_candidate(c,p,RECIPE,compliance=comp,behavior_baseline=bb,economics=econ,diagnostics=dx,outcome_history=outcomes())
    assert out["eligibility"]=="BLOCKED"
    assert any("diagnostic" in x for x in out["blockers"])


def test_poor_historical_verification_blocks_candidate(tmp_path):
    bb,rb,costs,rp=fixtures(tmp_path); c,p,comp,econ=current(tmp_path,"excessive_rinse",rb,costs,rp)
    weak=OutcomeHistorySummary(comparable_cycles=35,cycles_with_verification=30,passed_verifications=27,failed_verifications=3)
    out=discover_final_rinse_candidate(c,p,RECIPE,compliance=comp,behavior_baseline=bb,economics=econ,diagnostics=clean_dx(),outcome_history=weak)
    assert out["eligibility"]=="BLOCKED"
    assert any("pass rate" in x for x in out["blockers"])


def test_controlled_trial_never_auto_accepts_even_when_results_are_good(tmp_path):
    bb,rb,costs,rp=fixtures(tmp_path); c,p,comp,econ=current(tmp_path,"excessive_rinse",rb,costs,rp)
    cand=discover_final_rinse_candidate(c,p,RECIPE,compliance=comp,behavior_baseline=bb,economics=econ,diagnostics=clean_dx(),outcome_history=outcomes())
    results=[TrialCycleResult(cycle_id=f"t{i}",l2_assessment="COMPLIANT",verification_outcome="PASS",diagnostic_status="NO_FINDINGS",measured_savings={"water_m3":.8}) for i in range(10)]
    ass=assess_controlled_trial(cand,results,engineering_approval_ref="ENG-1",qa_approval_ref="QA-1",protocol_ref="VAL-1")
    assert ass["assessment"]=="EVIDENCE_SUPPORTS_HUMAN_REVIEW"
    assert ass["governance"]["automatic_recipe_acceptance"] is False
    assert ass["aggregate_measured_savings"]["water_m3"]==8.0


def test_trial_without_approvals_cannot_support_adoption(tmp_path):
    bb,rb,costs,rp=fixtures(tmp_path); c,p,comp,econ=current(tmp_path,"excessive_rinse",rb,costs,rp)
    cand=discover_final_rinse_candidate(c,p,RECIPE,compliance=comp,behavior_baseline=bb,economics=econ,diagnostics=clean_dx(),outcome_history=outcomes())
    results=[TrialCycleResult(cycle_id=f"t{i}",l2_assessment="COMPLIANT",verification_outcome="PASS") for i in range(10)]
    ass=assess_controlled_trial(cand,results)
    assert ass["assessment"]=="INSUFFICIENT_TRIAL_EVIDENCE"
    assert any("approval" in x for x in ass["blockers"])


def test_any_trial_verification_failure_triggers_reject_or_investigate(tmp_path):
    bb,rb,costs,rp=fixtures(tmp_path); c,p,comp,econ=current(tmp_path,"excessive_rinse",rb,costs,rp)
    cand=discover_final_rinse_candidate(c,p,RECIPE,compliance=comp,behavior_baseline=bb,economics=econ,diagnostics=clean_dx(),outcome_history=outcomes())
    results=[TrialCycleResult(cycle_id=f"t{i}",l2_assessment="COMPLIANT",verification_outcome="PASS") for i in range(9)] + [TrialCycleResult(cycle_id="bad",l2_assessment="COMPLIANT",verification_outcome="FAIL")]
    ass=assess_controlled_trial(cand,results,engineering_approval_ref="E",qa_approval_ref="Q",protocol_ref="P")
    assert ass["assessment"]=="REJECT_OR_INVESTIGATE"
    assert ass["verification_failures"]==1


def test_demo_endpoint_returns_m8_candidate():
    r=TestClient(app).get('/v1/demo/optimization/excessive_rinse')
    assert r.status_code==200
    assert r.json()['candidate']['eligibility']=='ELIGIBLE_FOR_CONTROLLED_VALIDATION'


def test_health_reports_m8_api_version():
    r=TestClient(app).get('/health')
    assert r.status_code==200
    assert r.json()['api_version']=='1.1.0'
