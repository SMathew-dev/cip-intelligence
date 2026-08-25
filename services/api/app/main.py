from __future__ import annotations

import csv
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import FastAPI, File, HTTPException, UploadFile, Request, Header
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.production import Settings, ProductionDB, JobRunner, identity, require_role

from app.ingestion.models import MappingProfile
from app.acquisition.models import AcquisitionSource
from app.acquisition.service import AcquisitionService, UnsupportedAdapterError
from app.ingestion.service import IngestionService
from app.intelligence.compliance import minimum_requirement
from app.intelligence.data_quality import detect_flatline
from app.simulator import generate_cycle
from app.reconstruction.engine import reconstruct_cycles
from app.reconstruction.models import SignalPoint
from app.reconstruction.service import ReconstructionService
from app.compliance.models import ValidatedRecipe
from app.compliance.service import ComplianceService
from app.behavior.models import BehaviorBaselineRequest, BehaviorPolicy
from app.behavior.service import BehaviorService
from app.behavior.features import extract_behavior_features
from app.behavior.engine import build_baseline, evaluate_behavior
from app.economics.models import CostProfile, ResourceBaselineRequest
from app.economics.service import EconomicsService
from app.production_context.models import ContextBaselineRequest, ProductionRun
from app.production_context.service import ProductionContextService
from app.diagnostics.models import QAResult, MaintenanceEvent, OperatorObservation, DiagnosticCase, DiagnosisPolicy
from app.diagnostics.service import DiagnosticService
from app.optimization.models import ControlledTrialAssessmentRequest, OptimizationDecisionRecord
from app.optimization.service import OptimizationService
from app.ui_demo import demo_data_health, demo_overview, demo_timeseries

REPO_ROOT = Path(__file__).resolve().parents[3]
settings = Settings.load(REPO_ROOT)
prod_db = ProductionDB(settings.runtime_dir / "production.sqlite3")
job_runner = JobRunner(prod_db)
app = FastAPI(title="CIP Intelligence API", version="1.1.0")
if settings.allowed_origins:
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=False, allow_methods=["GET","POST"], allow_headers=["Authorization","Content-Type","X-Request-ID"])
ingestion_service = IngestionService(settings.runtime_dir)
acquisition_service = AcquisitionService(settings.runtime_dir, ingestion_service=ingestion_service)
reconstruction_service = ReconstructionService(settings.runtime_dir)
compliance_service = ComplianceService(settings.runtime_dir)
behavior_service = BehaviorService(settings.runtime_dir)
economics_service = EconomicsService(settings.runtime_dir)
production_context_service = ProductionContextService(settings.runtime_dir)
diagnostic_service = DiagnosticService(settings.runtime_dir)
optimization_service = OptimizationService(settings.runtime_dir)


@app.middleware("http")
async def production_guardrails(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(__import__("uuid").uuid4())
    try: actor, role = identity(settings, request.headers.get("Authorization"))
    except HTTPException: actor, role = "anonymous", "none"
    response = await call_next(request)
    for k,v in {"X-Request-ID":request_id,"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"no-referrer","Content-Security-Policy":"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'"}.items(): response.headers[k]=v
    if request.method != "GET" or request.url.path.startswith("/v1/"):
        prod_db.audit(id=str(__import__("uuid").uuid4()), ts=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), actor=actor, role=role, method=request.method, path=request.url.path, status=response.status_code, request_id=request_id)
    return response

@app.get("/ready")
def readiness() -> dict:
    try:
        with prod_db.connect() as c: c.execute("SELECT 1").fetchone()
        return {"status":"ready","operational_store":"ok","mode":"read_only","auth_enabled":settings.auth_enabled}
    except Exception as exc: raise HTTPException(503, f"operational store unavailable: {exc}")

@app.get("/v1/admin/audit")
def audit_log(limit: int = 100, authorization: str | None = Header(default=None)) -> dict:
    require_role(settings, authorization, "admin"); return {"events":prod_db.audits(max(1,min(limit,500)))}

@app.get("/v1/jobs")
def production_jobs(authorization: str | None = Header(default=None)) -> dict:
    require_role(settings, authorization, "engineer"); return {"jobs":prod_db.jobs()}

@app.post("/v1/connectors")
def save_connector(payload: dict, authorization: str | None = Header(default=None)) -> dict:
    require_role(settings, authorization, "admin")
    name,kind=payload.get("name"),payload.get("kind")
    if not name or not kind: raise HTTPException(422,"name and kind are required")
    if payload.get("mode","read_only") != "read_only": raise HTTPException(422,"CIP Intelligence connectors are read-only")
    allowed={"watched_folder","historian_api","sql_readonly","opcua_readonly","lims_api","mes_api","cmms_api"}
    if kind not in allowed: raise HTTPException(422,f"connector kind must be one of {sorted(allowed)}")
    try: cid=prod_db.save_connector(name,kind,payload.get("config",{}))
    except Exception as exc: raise HTTPException(409,f"connector could not be saved: {exc}")
    return {"id":cid,"name":name,"kind":kind,"mode":"read_only"}

@app.get("/v1/connectors")
def list_connectors(authorization: str | None = Header(default=None)) -> dict:
    require_role(settings, authorization, "engineer"); return {"connectors":prod_db.connectors()}


@app.get("/", include_in_schema=False)
def app_root() -> RedirectResponse:
    return RedirectResponse(url="/app/")


@app.get("/v1/demo/ui/overview")
def ui_overview_demo() -> dict:
    return demo_overview()


@app.get("/v1/demo/ui/data-health")
def ui_data_health_demo() -> dict:
    return demo_data_health()


@app.get("/v1/demo/ui/timeseries/{scenario}")
def ui_timeseries_demo(scenario: str) -> dict:
    try:
        return demo_timeseries(scenario)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "product": "CIP Intelligence",
        "architecture": "v1",
        "api_version": "1.1.0",
    }


@app.post("/v1/ingestion/inspect")
async def inspect_upload(file: UploadFile = File(...)) -> dict:
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail="Milestone 1A currently accepts CSV files. XLSX is next.")
    content = await file.read()
    try:
        result = ingestion_service.inspect(content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"filename": file.filename, **result}


@app.post("/v1/mappings")
def save_mapping(profile: MappingProfile) -> dict:
    try:
        return ingestion_service.save_mapping(profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/mappings")
def list_mappings() -> dict:
    return {"profiles": ingestion_service.mapping_store.list()}


@app.post("/v1/ingestion/{profile_name}")
async def ingest_upload(profile_name: str, file: UploadFile = File(...)) -> dict:
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail="Milestone 1A currently accepts CSV files. XLSX is next.")
    content = await file.read()
    try:
        return ingestion_service.ingest(content, file.filename or "upload.csv", profile_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Mapping profile {profile_name!r} was not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/acquisition/sources")
def save_acquisition_source(source: AcquisitionSource) -> dict:
    try:
        return acquisition_service.save_source(source)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Mapping profile {source.mapping_profile!r} was not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/acquisition/sources")
def list_acquisition_sources() -> dict:
    return {"sources": [s.model_dump(mode="json") for s in acquisition_service.source_store.list()]}


@app.post("/v1/acquisition/sources/{source_name}/run")
def run_acquisition_source(source_name: str) -> dict:
    try:
        return acquisition_service.run_source(source_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UnsupportedAdapterError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


@app.get("/v1/acquisition/jobs")
def list_acquisition_jobs(source_name: str | None = None) -> dict:
    return {"jobs": [j.model_dump(mode="json") for j in acquisition_service.job_store.list(source_name)]}


@app.post("/v1/acquisition/jobs/{job_id}/retry")
def retry_acquisition_job(job_id: str) -> dict:
    try:
        return acquisition_service.retry_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UnsupportedAdapterError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


@app.post("/v1/reconstruction/ingestions/{ingestion_id}")
def reconstruct_ingestion(ingestion_id: str) -> dict:
    try:
        return reconstruction_service.reconstruct_ingestion(ingestion_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/compliance/recipes")
def save_validated_recipe(recipe: ValidatedRecipe) -> dict:
    try:
        return compliance_service.save_recipe(recipe)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/compliance/recipes")
def list_validated_recipes() -> dict:
    return {"recipes": [r.model_dump(mode="json") for r in compliance_service.recipe_store.list()]}


@app.post("/v1/compliance/ingestions/{ingestion_id}")
def evaluate_ingestion_compliance(ingestion_id: str, recipe_name: str | None = None) -> dict:
    try:
        return compliance_service.evaluate_ingestion(ingestion_id, recipe_name=recipe_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/behavior/baselines")
def create_behavior_baseline(request: BehaviorBaselineRequest) -> dict:
    try:
        return behavior_service.build_baseline(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/behavior/baselines")
def list_behavior_baselines() -> dict:
    return {"baselines": behavior_service.baseline_store.list()}


@app.post("/v1/behavior/ingestions/{ingestion_id}")
def evaluate_ingestion_behavior(ingestion_id: str, baseline_name: str, baseline_revision: str) -> dict:
    try:
        return behavior_service.evaluate_ingestion(
            ingestion_id, baseline_name=baseline_name, baseline_revision=baseline_revision
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/economics/cost-profiles")
def save_cost_profile(profile: CostProfile) -> dict:
    try:
        return economics_service.save_cost_profile(profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/economics/cost-profiles")
def list_cost_profiles() -> dict:
    return {"cost_profiles": economics_service.store.list("cost_profiles")}


@app.post("/v1/economics/baselines")
def save_resource_baseline(request: ResourceBaselineRequest) -> dict:
    try:
        return economics_service.build_baseline(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/economics/baselines")
def list_resource_baselines() -> dict:
    return {"baselines": economics_service.store.list("baselines")}


@app.post("/v1/economics/ingestions/{ingestion_id}")
def evaluate_ingestion_economics(ingestion_id: str, baseline_name: str, baseline_revision: str, cost_profile_name: str, cost_profile_revision: str) -> dict:
    try:
        return economics_service.evaluate_ingestion(
            ingestion_id, baseline_name=baseline_name, baseline_revision=baseline_revision,
            cost_profile_name=cost_profile_name, cost_profile_revision=cost_profile_revision,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/context/production-runs")
def save_production_run(run: ProductionRun) -> dict:
    try:
        return production_context_service.save_production_run(run)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/context/production-runs")
def list_production_runs(asset: str | None = None) -> dict:
    return {"production_runs": production_context_service.list_production_runs(asset=asset)}


@app.post("/v1/context/baselines")
def create_context_baseline(request: ContextBaselineRequest) -> dict:
    try:
        return production_context_service.build_baseline(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/context/baselines")
def list_context_baselines() -> dict:
    return {"baselines": production_context_service.baseline_store.list()}


@app.post("/v1/context/ingestions/{ingestion_id}")
def evaluate_ingestion_context(ingestion_id: str, baseline_name: str, baseline_revision: str) -> dict:
    try:
        return production_context_service.evaluate_ingestion(
            ingestion_id, baseline_name=baseline_name, baseline_revision=baseline_revision
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/demo/context/{scenario}")
def context_demo(scenario: str) -> dict:
    from app.production_context.demo import demo_context
    try:
        return demo_context(REPO_ROOT, scenario)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/diagnostics/qa-results")
def save_qa_result(result: QAResult) -> dict:
    try:
        return diagnostic_service.save_qa(result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/diagnostics/qa-results")
def list_qa_results(asset: str | None = None) -> dict:
    return {"qa_results": diagnostic_service.qa_store.list(asset=asset)}


@app.post("/v1/diagnostics/maintenance-events")
def save_maintenance_event(event: MaintenanceEvent) -> dict:
    try:
        return diagnostic_service.save_maintenance(event)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/diagnostics/maintenance-events")
def list_maintenance_events(asset: str | None = None) -> dict:
    return {"maintenance_events": diagnostic_service.maintenance_store.list(asset=asset)}


@app.post("/v1/diagnostics/operator-observations")
def save_operator_observation(observation: OperatorObservation) -> dict:
    try:
        return diagnostic_service.save_observation(observation)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/diagnostics/operator-observations")
def list_operator_observations(asset: str | None = None) -> dict:
    return {"operator_observations": diagnostic_service.observation_store.list(asset=asset)}


@app.post("/v1/diagnostics/cases")
def save_diagnostic_case(case: DiagnosticCase) -> dict:
    try:
        return diagnostic_service.save_case(case)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/diagnostics/cases")
def list_diagnostic_cases(asset: str | None = None) -> dict:
    return {"cases": diagnostic_service.case_store.list(asset=asset)}


@app.post("/v1/diagnostics/ingestions/{ingestion_id}")
def evaluate_ingestion_diagnostics(ingestion_id: str, policy: DiagnosisPolicy | None = None) -> dict:
    try:
        return diagnostic_service.evaluate_ingestion(ingestion_id, policy=policy)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/demo/diagnostics/{scenario}")
def diagnostics_demo(scenario: str) -> dict:
    from app.diagnostics.demo import demo_diagnostic
    try:
        return demo_diagnostic(REPO_ROOT, scenario)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/optimization/candidates")
def save_optimization_candidate(candidate: dict) -> dict:
    try:
        if not candidate.get("candidate_id"):
            raise ValueError("candidate_id is required")
        return optimization_service.save_candidate(candidate)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/optimization/candidates")
def list_optimization_candidates() -> dict:
    return {"candidates": optimization_service.store.list("candidates")}


@app.post("/v1/optimization/decisions")
def save_optimization_decision(decision: OptimizationDecisionRecord) -> dict:
    try:
        return optimization_service.save_decision(decision)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/optimization/trials/assess")
def assess_optimization_trial(request: ControlledTrialAssessmentRequest) -> dict:
    try:
        return optimization_service.assess_trial(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/demo/optimization/{scenario}")
def optimization_demo(scenario: str) -> dict:
    from app.optimization.demo import demo_optimization
    try:
        return demo_optimization(REPO_ROOT, scenario)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/demo/reconstruct/{scenario}")
def reconstruct_demo(scenario: str, mode: str = "explicit") -> dict:
    allowed = {"normal", "low_temp", "low_flow", "sensor_freeze", "excessive_rinse", "compliant_low_flow", "profile_shift", "context_long_run_response"}
    if scenario not in allowed:
        raise HTTPException(status_code=422, detail=f"scenario must be one of {sorted(allowed)}")
    if mode not in {"explicit", "inferred"}:
        raise HTTPException(status_code=422, detail="mode must be 'explicit' or 'inferred'")

    with NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        path = Path(tmp.name)
    generate_cycle(path, scenario=scenario)
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    points = [
        SignalPoint(
            ts=__import__("datetime").datetime.fromisoformat(r["timestamp"]),
            asset=r["asset"],
            return_temperature_c=float(r["return_temperature_c"]),
            return_flow_lpm=float(r["return_flow_lpm"]),
            return_conductivity_mscm=float(r["return_conductivity_mscm"]),
            return_pressure_bar=float(r["return_pressure_bar"]),
            explicit_phase=r["phase"] if mode == "explicit" else None,
        )
        for r in rows
    ]
    return {"scenario": scenario, "mode": mode, **reconstruct_cycles(points)}


@app.get("/v1/demo/compliance/{scenario}")
def compliance_demo(scenario: str, mode: str = "explicit") -> dict:
    allowed = {"normal", "low_temp", "low_flow", "sensor_freeze", "excessive_rinse", "compliant_low_flow", "profile_shift", "context_long_run_response"}
    if scenario not in allowed:
        raise HTTPException(status_code=422, detail=f"scenario must be one of {sorted(allowed)}")
    if mode not in {"explicit", "inferred"}:
        raise HTTPException(status_code=422, detail="mode must be 'explicit' or 'inferred'")

    from app.compliance.engine import evaluate_cycle

    with NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        path = Path(tmp.name)
    generate_cycle(path, scenario=scenario)
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    points = [
        SignalPoint(
            ts=__import__("datetime").datetime.fromisoformat(r["timestamp"]),
            asset=r["asset"],
            return_temperature_c=float(r["return_temperature_c"]),
            return_flow_lpm=float(r["return_flow_lpm"]),
            return_conductivity_mscm=float(r["return_conductivity_mscm"]),
            return_pressure_bar=float(r["return_pressure_bar"]),
            explicit_phase=r["phase"] if mode == "explicit" else None,
        )
        for r in rows
    ]
    reconstructed = reconstruct_cycles(points)
    if reconstructed["cycle_count"] != 1:
        return {"scenario": scenario, "mode": mode, "reconstruction": reconstructed}
    recipe_path = REPO_ROOT / "config" / "example_htst_validated_recipe_v7.json"
    recipe = ValidatedRecipe.model_validate_json(recipe_path.read_text(encoding="utf-8"))
    return {"scenario": scenario, "mode": mode, **evaluate_cycle(reconstructed["cycles"][0], points, recipe)}


@app.get("/v1/demo/behavior/{scenario}")
def behavior_demo(scenario: str) -> dict:
    from datetime import timedelta
    from app.compliance.engine import evaluate_cycle

    allowed = {"normal", "compliant_low_flow", "profile_shift", "excessive_rinse", "low_temp", "sensor_freeze"}
    if scenario not in allowed:
        raise HTTPException(status_code=422, detail=f"scenario must be one of {sorted(allowed)}")

    recipe_path = REPO_ROOT / "config" / "example_htst_validated_recipe_v7.json"
    recipe = ValidatedRecipe.model_validate_json(recipe_path.read_text(encoding="utf-8"))
    policy = BehaviorPolicy(minimum_baseline_cycles=20, minimum_feature_cycles=15)
    candidates = []

    def points_from_file(path: Path) -> list[SignalPoint]:
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
                explicit_phase=r["phase"],
            )
            for r in rows
        ]

    # Thirty independent normal cycles create the development baseline. Each has
    # a distinct timestamp so lineage/cycle identity remains honest.
    for i in range(30):
        with NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            path = Path(tmp.name)
        generate_cycle(
            path, scenario="normal", seed=100 + i,
            start=recipe.effective_from + timedelta(days=i + 1),
        )
        pts = points_from_file(path)
        reconstruction = reconstruct_cycles(pts)["cycles"][0]
        compliance = evaluate_cycle(reconstruction, pts, recipe)
        candidates.append({
            "ingestion_id": f"demo-history-{i:03d}",
            "cycle_id": reconstruction["cycle_id"],
            "start_ts": reconstruction["start_ts"],
            "eligible": compliance["overall_assessment"] == "COMPLIANT",
            "eligibility_reason": "eligible" if compliance["overall_assessment"] == "COMPLIANT" else compliance["overall_assessment"],
            "features": extract_behavior_features(reconstruction, pts, profile_bins=policy.profile_bins),
            "normalized_sha256": f"demo-normalized-{i}",
            "reconstruction_sha256": f"demo-reconstruction-{i}",
            "compliance_sha256": f"demo-compliance-{i}",
        })

    baseline = build_baseline(
        name="HTST-01-normal-behavior", revision="demo-1", asset="HTST-01",
        recipe_name=recipe.name, recipe_revision=recipe.revision, candidates=candidates, policy=policy,
        description="Deterministic simulator baseline for the L3 behavior demo.",
    )

    with NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        path = Path(tmp.name)
    generate_cycle(path, scenario=scenario, seed=7, start=recipe.effective_from + timedelta(days=60))
    pts = points_from_file(path)
    reconstruction = reconstruct_cycles(pts)["cycles"][0]
    compliance = evaluate_cycle(reconstruction, pts, recipe)
    features = extract_behavior_features(reconstruction, pts, profile_bins=policy.profile_bins)
    return {
        "scenario": scenario,
        "compliance": compliance["overall_assessment"],
        "behavior": evaluate_behavior(features, baseline, l2_assessment=compliance["overall_assessment"]),
        "baseline_summary": {
            "training_cycle_count": baseline["training_cycle_count"],
            "maturity": baseline["baseline_maturity"],
            "excluded_cycle_count": baseline["excluded_cycle_count"],
        },
    }


@app.get("/v1/demo/economics/{scenario}")
def economics_demo(scenario: str) -> dict:
    allowed = {"normal", "excessive_rinse"}
    if scenario not in allowed:
        raise HTTPException(status_code=422, detail=f"scenario must be one of {sorted(allowed)}")
    from app.economics.demo import demo_economics
    return demo_economics(REPO_ROOT, scenario)


@app.get("/v1/demo/analyze/{scenario}")
def analyze_demo(scenario: str) -> dict:
    allowed = {"normal", "low_temp", "low_flow", "sensor_freeze", "excessive_rinse", "compliant_low_flow", "profile_shift", "context_long_run_response"}
    if scenario not in allowed:
        return {"error": f"scenario must be one of {sorted(allowed)}"}

    with NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        path = Path(tmp.name)
    generate_cycle(path, scenario=scenario)

    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    caustic = [r for r in rows if r["phase"] == "CAUSTIC"]
    final_rinse = [r for r in rows if r["phase"] == "FINAL_RINSE"]
    flow = [float(r["return_flow_lpm"]) for r in caustic]
    temp = [float(r["return_temperature_c"]) for r in caustic]
    rinse_cond = [float(r["return_conductivity_mscm"]) for r in final_rinse]

    quality_issues = [i.__dict__ for i in detect_flatline(flow)]
    flow_reliable = not any(i["code"] == "FLATLINE" for i in quality_issues)

    findings = []
    findings.append(minimum_requirement(
        code="CIP-TEMP-MIN",
        title="Caustic temperature exposure",
        actual=min(temp),
        minimum=72.0,
        unit="C",
    ).__dict__)

    if flow_reliable:
        findings.append(minimum_requirement(
            code="CIP-FLOW-MIN",
            title="Caustic return flow",
            actual=min(flow),
            minimum=380.0,
            unit="L/min",
        ).__dict__)
    else:
        findings.append({
            "code": "CIP-FLOW-UNKNOWN",
            "finding_class": "UNKNOWN",
            "severity": "WARNING",
            "title": "Caustic return flow",
            "conclusion": "Flow-dependent compliance is unavailable because the flow signal is unreliable.",
            "confidence": None,
            "evidence": {"quality_issues": quality_issues},
        })

    rinse_minutes = len(final_rinse) / 6
    endpoint_reached_at = next((idx / 6 for idx, v in enumerate(rinse_cond) if v <= 1.5), None)
    if endpoint_reached_at is not None and rinse_minutes - endpoint_reached_at >= 3:
        findings.append({
            "code": "RINSE-OPT-CANDIDATE",
            "finding_class": "INFERRED",
            "severity": "INFO",
            "title": "Final rinse optimization candidate",
            "conclusion": "The configured conductivity endpoint was reached materially before the rinse ended. Review as a controlled validation candidate; do not automatically shorten the recipe.",
            "confidence": 0.85,
            "evidence": {
                "rinse_duration_min": round(rinse_minutes, 2),
                "endpoint_reached_min": round(endpoint_reached_at, 2),
                "endpoint_mscm": 1.5,
            },
        })

    overall = "COMPLIANT"
    if any(f["severity"] == "CRITICAL" for f in findings):
        overall = "PROCESS_DEVIATION"
    elif quality_issues:
        overall = "DATA_REVIEW_REQUIRED"

    return {
        "scenario": scenario,
        "overall_assessment": overall,
        "data_quality": {"issues": quality_issues, "flow_reliable": flow_reliable},
        "findings": findings,
        "principle": "Evidence before AI; unknown is an allowed result.",
    }


STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/app", StaticFiles(directory=STATIC_DIR, html=True), name="app-ui")
