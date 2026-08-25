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
from .engine import evaluate_diagnostics, link_evidence
from .models import QAResult, MaintenanceEvent, DiagnosisPolicy

def _points(path:Path):
    from datetime import datetime
    with path.open(encoding="utf-8") as f: rows=list(csv.DictReader(f))
    return [SignalPoint(ts=datetime.fromisoformat(r["timestamp"]),asset=r["asset"],return_temperature_c=float(r["return_temperature_c"]),return_flow_lpm=float(r["return_flow_lpm"]),return_conductivity_mscm=float(r["return_conductivity_mscm"]),return_pressure_bar=float(r["return_pressure_bar"]),explicit_phase=r["phase"]) for r in rows]

def demo_diagnostic(repo_root:Path,scenario:str)->dict:
    if scenario not in {"verification_failure","restriction","restriction_confirmed","sensor_freeze","normal"}:raise ValueError("diagnostic demo scenario unsupported")
    sim={"verification_failure":"normal","restriction":"low_flow","restriction_confirmed":"low_flow","sensor_freeze":"sensor_freeze","normal":"normal"}[scenario]
    recipe=ValidatedRecipe.model_validate_json((repo_root/"config"/"example_htst_validated_recipe_v7.json").read_text())
    with NamedTemporaryFile(suffix=".csv",delete=False) as t:path=Path(t.name)
    generate_cycle(path,scenario=sim,seed=902,start=recipe.effective_from+timedelta(days=90));pts=_points(path);cycle=reconstruct_cycles(pts)["cycles"][0];comp=evaluate_cycle(cycle,pts,recipe);end=__import__("datetime").datetime.fromisoformat(cycle["end_ts"])
    qa=[];maint=[]
    if scenario=="verification_failure":qa=[QAResult(result_id="qa-demo-1",asset="HTST-01",sample_ts=end+timedelta(minutes=20),result_type="ATP",outcome="FAIL",value=340,unit="RLU",source_type="SIMULATOR",source_ref="sim://qa-demo-1")]
    if scenario=="restriction_confirmed":maint=[MaintenanceEvent(event_id="m-demo-1",asset="HTST-01",event_ts=end+timedelta(hours=2),component="V-214",action="inspection",finding_code="DX-HYD-RESTRICTION",finding_text="Return valve found partially obstructed during simulator inspection.",confirmation_status="CONFIRMED",source_type="SIMULATOR",source_ref="sim://m-demo-1")]
    linked=link_evidence(cycle,qa,maint,[],DiagnosisPolicy())
    behavior=None
    if scenario in {"restriction", "restriction_confirmed"}:
        behavior={"deviations":[
            {"feature":"phase.CAUSTIC.return_flow_lpm.median","direction":"LOW","severity":"HIGH"},
            {"feature":"phase.CAUSTIC.return_pressure_bar.median","direction":"HIGH","severity":"HIGH"},
        ],"profile_deviations":[],"demo_note":"Synthetic L3 directional evidence for the diagnostic simulator only."}
    return {"scenario":scenario,"l2":comp["overall_assessment"],"l5":evaluate_diagnostics(cycle,pts,compliance=comp,behavior=behavior,linked=linked),"demo_boundary":"Simulator evidence only; diagnoses are not universal dairy failure thresholds."}
