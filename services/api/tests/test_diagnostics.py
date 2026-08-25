from __future__ import annotations
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
import pytest

from app.compliance.engine import evaluate_cycle
from app.compliance.models import ValidatedRecipe
from app.diagnostics.engine import evaluate_diagnostics, link_evidence
from app.diagnostics.models import QAResult, MaintenanceEvent, OperatorObservation, DiagnosticCase, DiagnosisPolicy
from app.diagnostics.store import JsonRecordStore
from app.reconstruction.engine import reconstruct_cycles
from app.reconstruction.models import SignalPoint
from app.simulator import generate_cycle

ROOT = Path(__file__).resolve().parents[3]
RECIPE = ValidatedRecipe.model_validate_json((ROOT / "config" / "example_htst_validated_recipe_v7.json").read_text())

def cycle_data(scenario="normal"):
    with NamedTemporaryFile(suffix=".csv", delete=False) as t: path=Path(t.name)
    generate_cycle(path, scenario=scenario, seed=222, start=RECIPE.effective_from + timedelta(days=100))
    with path.open() as f: rows=list(csv.DictReader(f))
    pts=[SignalPoint(ts=datetime.fromisoformat(r["timestamp"]),asset=r["asset"],return_temperature_c=float(r["return_temperature_c"]),return_flow_lpm=float(r["return_flow_lpm"]),return_conductivity_mscm=float(r["return_conductivity_mscm"]),return_pressure_bar=float(r["return_pressure_bar"]),explicit_phase=r["phase"]) for r in rows]
    cycle=reconstruct_cycles(pts)["cycles"][0]
    return cycle,pts,evaluate_cycle(cycle,pts,RECIPE)

def fake_behavior(*, flow="LOW", pressure="HIGH"):
    return {"deviations":[
        {"feature":"phase.CAUSTIC.return_flow_lpm.median","direction":flow,"severity":"HIGH"},
        {"feature":"phase.CAUSTIC.return_pressure_bar.median","direction":pressure,"severity":"HIGH"},
    ],"profile_deviations":[]}

def test_qa_failure_is_outcome_not_proven_root_cause():
    c,p,comp=cycle_data("normal"); end=datetime.fromisoformat(c["end_ts"])
    q=QAResult(result_id="q1",asset="HTST-01",sample_ts=end+timedelta(minutes=20),result_type="ATP",outcome="FAIL",value=300,unit="RLU",source_type="SIMULATOR",source_ref="sim://q1")
    linked=link_evidence(c,[q],[],[],DiagnosisPolicy())
    out=evaluate_diagnostics(c,p,compliance=comp,linked=linked)
    assert any(x["code"]=="OUTCOME-VERIFICATION-FAIL" for x in out["detections"])
    assert any(x["code"]=="DX-CLEANABILITY-001" for x in out["hypotheses"])
    assert not out["confirmed_conditions"]

def test_unlinked_qa_result_is_not_attached():
    c,_,_=cycle_data(); end=datetime.fromisoformat(c["end_ts"])
    q=QAResult(result_id="q2",asset="HTST-01",sample_ts=end+timedelta(days=2),result_type="ATP",outcome="FAIL",source_type="SIMULATOR",source_ref="sim://q2")
    assert link_evidence(c,[q],[],[],DiagnosisPolicy())["qa_results"] == []

def test_wrong_asset_evidence_is_not_attached():
    c,_,_=cycle_data(); end=datetime.fromisoformat(c["end_ts"])
    q=QAResult(result_id="q3",asset="VAT-01",sample_ts=end+timedelta(minutes=5),result_type="ATP",outcome="FAIL",source_type="SIMULATOR",source_ref="sim://q3")
    assert link_evidence(c,[q],[],[],DiagnosisPolicy())["qa_results"] == []

def test_restriction_requires_joint_plant_specific_flow_pressure_evidence():
    c,p,comp=cycle_data("low_flow")
    out=evaluate_diagnostics(c,p,compliance=comp,behavior=fake_behavior())
    assert any(x["code"]=="DX-HYD-RESTRICTION" for x in out["hypotheses"])

def test_low_flow_without_pressure_baseline_does_not_claim_restriction():
    c,p,comp=cycle_data("low_flow")
    out=evaluate_diagnostics(c,p,compliance=comp)
    assert not any(x["code"]=="DX-HYD-RESTRICTION" for x in out["hypotheses"])
    assert any(x["code"]=="DET-HYD-FLOW" for x in out["detections"])

def test_low_flow_low_pressure_supports_pump_hypothesis():
    c,p,comp=cycle_data("low_flow")
    out=evaluate_diagnostics(c,p,compliance=comp,behavior=fake_behavior(pressure="LOW"))
    assert any(x["code"]=="DX-HYD-PUMP" for x in out["hypotheses"])

def test_flatline_blocks_hydraulic_root_cause():
    c,p,comp=cycle_data("sensor_freeze")
    out=evaluate_diagnostics(c,p,compliance=comp,behavior=fake_behavior())
    assert any(x["code"]=="FM-INS-001" for x in out["detections"])
    assert not any(x["code"].startswith("DX-HYD") for x in out["hypotheses"])

def test_maintenance_confirmation_is_confirmed_not_inferred():
    c,p,comp=cycle_data("low_flow"); end=datetime.fromisoformat(c["end_ts"])
    m=MaintenanceEvent(event_id="m1",asset="HTST-01",event_ts=end+timedelta(hours=1),component="V-214",action="inspect",finding_code="DX-HYD-RESTRICTION",finding_text="Physical obstruction found.",confirmation_status="CONFIRMED",source_type="SIMULATOR",source_ref="sim://m1")
    linked=link_evidence(c,[],[m],[],DiagnosisPolicy())
    out=evaluate_diagnostics(c,p,compliance=comp,behavior=fake_behavior(),linked=linked)
    assert any(x["code"]=="DX-HYD-RESTRICTION" and x["class"]=="CONFIRMED" for x in out["confirmed_conditions"])
    assert not any(x["code"]=="DX-HYD-RESTRICTION" for x in out["hypotheses"])

def test_operator_observation_links_around_cycle():
    c,_,_=cycle_data(); start=datetime.fromisoformat(c["start_ts"])
    o=OperatorObservation(observation_id="o1",asset="HTST-01",event_ts=start-timedelta(minutes=20),category="VALVE",text="Valve slow to seat",source_ref="manual://o1")
    assert len(link_evidence(c,[],[],[o],DiagnosisPolicy())["operator_observations"])==1

def test_historical_confirmations_only_upgrade_confidence_after_policy_threshold():
    c,p,comp=cycle_data("low_flow")
    cases=[{"case_id":f"c{i}","asset":"HTST-01","diagnosis_code":"DX-HYD-RESTRICTION","confirmation_status":"CONFIRMED","confirmed_code":"DX-HYD-RESTRICTION"} for i in range(5)]
    out=evaluate_diagnostics(c,p,compliance=comp,behavior=fake_behavior(),historical_cases=cases,policy=DiagnosisPolicy(minimum_historical_confirmations=5))
    dx=next(x for x in out["hypotheses"] if x["code"]=="DX-HYD-RESTRICTION")
    assert dx["confidence"]=="HIGH"
    assert dx["evidence"]["historical_support"]["empirical_precision"]==1.0

def test_negative_history_prevents_confidence_upgrade():
    c,p,comp=cycle_data("low_flow")
    cases=[]
    for i in range(10): cases.append({"case_id":f"n{i}","asset":"HTST-01","diagnosis_code":"DX-HYD-RESTRICTION","confirmation_status":"CONFIRMED" if i<5 else "NOT_CONFIRMED","confirmed_code":"DX-HYD-RESTRICTION" if i<5 else None})
    out=evaluate_diagnostics(c,p,compliance=comp,behavior=fake_behavior(),historical_cases=cases,policy=DiagnosisPolicy(minimum_historical_confirmations=5,minimum_empirical_precision=.6))
    dx=next(x for x in out["hypotheses"] if x["code"]=="DX-HYD-RESTRICTION")
    assert dx["confidence"]=="MODERATE"

def test_record_store_is_immutable(tmp_path):
    store=JsonRecordStore(tmp_path,"result_id")
    q=QAResult(result_id="q1",asset="A",sample_ts=datetime.now(timezone.utc),result_type="ATP",outcome="PASS",source_type="SIMULATOR",source_ref="sim://1")
    assert store.save(q)["duplicate"] is False
    assert store.save(q)["duplicate"] is True
    q2=q.model_copy(update={"outcome":"FAIL"})
    with pytest.raises(ValueError): store.save(q2)

def test_evidence_timestamps_must_be_timezone_aware():
    with pytest.raises(ValueError): QAResult(result_id="q",asset="A",sample_ts=datetime(2026,1,1),result_type="ATP",outcome="PASS",source_type="MANUAL",source_ref="x")

def test_evidence_graph_connects_failed_verification_to_investigation_hypothesis():
    c,p,comp=cycle_data("normal"); end=datetime.fromisoformat(c["end_ts"])
    q=QAResult(result_id="qgraph",asset="HTST-01",sample_ts=end+timedelta(minutes=10),result_type="ATP",outcome="FAIL",source_type="SIMULATOR",source_ref="sim://qgraph")
    linked=link_evidence(c,[q],[],[],DiagnosisPolicy())
    out=evaluate_diagnostics(c,p,compliance=comp,linked=linked)
    assert any(e["relationship"]=="TRIGGERS_INVESTIGATION" for e in out["evidence_graph"]["edges"])

def test_diagnostic_demo_verification_failure_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app
    r=TestClient(app).get('/v1/demo/diagnostics/verification_failure')
    assert r.status_code==200
    body=r.json()
    assert body['l5']['diagnostic_status']=='HYPOTHESES_AVAILABLE'
    assert any(x['code']=='OUTCOME-VERIFICATION-FAIL' for x in body['l5']['detections'])


def test_health_reports_m7_api_version():
    from fastapi.testclient import TestClient
    from app.main import app
    r=TestClient(app).get('/health')
    assert r.status_code==200
    assert r.json()['api_version']=='1.1.0'
