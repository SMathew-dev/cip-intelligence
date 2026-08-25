from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.compliance.engine import evaluate_cycle
from app.compliance.models import ValidatedRecipe
from app.compliance.store import RecipeStore
from app.reconstruction.engine import reconstruct_cycles
from app.reconstruction.models import SignalPoint
from app.simulator import generate_cycle


REPO_ROOT = Path(__file__).resolve().parents[3]
RECIPE_PATH = REPO_ROOT / "config" / "example_htst_validated_recipe_v7.json"


def recipe() -> ValidatedRecipe:
    return ValidatedRecipe.model_validate_json(RECIPE_PATH.read_text(encoding="utf-8"))


def scenario_points(tmp_path: Path, scenario: str, *, explicit: bool = True) -> list[SignalPoint]:
    path = generate_cycle(tmp_path / f"{scenario}.csv", scenario=scenario)
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [
        SignalPoint(
            ts=datetime.fromisoformat(r["timestamp"]),
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


def finding(result: dict, code: str) -> dict:
    return next(x for x in result["findings"] if x["code"] == code)


def test_normal_cycle_is_deterministically_compliant(tmp_path: Path) -> None:
    points = scenario_points(tmp_path, "normal")
    result = evaluate_cycle(reconstruct_one(points), points, recipe())
    assert result["overall_assessment"] == "COMPLIANT"
    assert result["requirements_failed"] == 0
    assert result["requirements_not_evaluable"] == 0
    assert finding(result, "HTST-CAUSTIC-VALIDATED-EXPOSURE")["status"] == "PASS"
    assert finding(result, "HTST-FINAL-RINSE-ENDPOINT")["status"] == "PASS"


def test_low_temperature_fails_concurrent_validated_exposure(tmp_path: Path) -> None:
    points = scenario_points(tmp_path, "low_temp")
    result = evaluate_cycle(reconstruct_one(points), points, recipe())
    f = finding(result, "HTST-CAUSTIC-VALIDATED-EXPOSURE")
    assert result["overall_assessment"] == "PROCESS_DEVIATION"
    assert f["status"] == "FAIL"
    assert f["evidence"]["qualified_seconds"] < 1200


def test_low_flow_fails_concurrent_validated_exposure(tmp_path: Path) -> None:
    points = scenario_points(tmp_path, "low_flow")
    result = evaluate_cycle(reconstruct_one(points), points, recipe())
    f = finding(result, "HTST-CAUSTIC-VALIDATED-EXPOSURE")
    assert result["overall_assessment"] == "PROCESS_DEVIATION"
    assert f["status"] == "FAIL"
    assert f["evidence"]["qualified_seconds"] < 1200


def test_frozen_flow_sensor_withholds_flow_dependent_compliance(tmp_path: Path) -> None:
    points = scenario_points(tmp_path, "sensor_freeze")
    result = evaluate_cycle(reconstruct_one(points), points, recipe())
    f = finding(result, "HTST-CAUSTIC-VALIDATED-EXPOSURE")
    assert result["overall_assessment"] == "DATA_REVIEW_REQUIRED"
    assert f["status"] == "NOT_EVALUABLE"
    assert any(x["metric"] == "return_flow_lpm" for x in f["evidence"]["flatline_flags"])


def test_inferred_phase_is_not_used_as_official_compliance_by_default(tmp_path: Path) -> None:
    points = scenario_points(tmp_path, "normal", explicit=False)
    result = evaluate_cycle(reconstruct_one(points), points, recipe())
    assert result["overall_assessment"] == "DATA_REVIEW_REQUIRED"
    assert result["requirements_not_evaluable"] == result["requirements_total"]
    assert all(x["status"] == "NOT_EVALUABLE" for x in result["findings"])


def test_missing_signal_coverage_is_not_silently_counted_as_failure_or_pass(tmp_path: Path) -> None:
    points = scenario_points(tmp_path, "normal")
    caustic_indices = [i for i, p in enumerate(points) if p.explicit_phase == "CAUSTIC"]
    damaged = list(points)
    for i in caustic_indices[:20]:  # >5% of phase duration
        damaged[i] = replace(damaged[i], return_flow_lpm=None)
    result = evaluate_cycle(reconstruct_one(damaged), damaged, recipe())
    f = finding(result, "HTST-CAUSTIC-VALIDATED-EXPOSURE")
    assert f["status"] == "NOT_EVALUABLE"
    assert f["evidence"]["data_coverage"] < 0.95


def test_endpoint_requires_sustained_tail_not_transient_crossing(tmp_path: Path) -> None:
    points = scenario_points(tmp_path, "normal")
    modified = []
    final = [p for p in points if p.explicit_phase == "FINAL_RINSE"]
    tail_start = final[-3].ts
    for p in points:
        if p.explicit_phase == "FINAL_RINSE" and p.ts >= tail_start:
            modified.append(replace(p, return_conductivity_mscm=2.0))
        else:
            modified.append(p)
    result = evaluate_cycle(reconstruct_one(modified), modified, recipe())
    f = finding(result, "HTST-FINAL-RINSE-ENDPOINT")
    assert f["status"] == "FAIL"
    assert f["evidence"]["tail_hold_seconds"] < 30


def test_approved_recipe_revision_is_immutable(tmp_path: Path) -> None:
    store = RecipeStore(tmp_path)
    first = recipe()
    saved = store.save(first)
    duplicate = store.save(first)
    assert saved["saved"] is True
    assert duplicate["duplicate"] is True

    payload = first.model_dump(mode="json")
    payload["approval_ref"] = "CHANGED-AFTER-APPROVAL"
    changed = ValidatedRecipe.model_validate(payload)
    with pytest.raises(ValueError, match="immutable"):
        store.save(changed)


def test_later_revision_supersedes_open_ended_older_revision(tmp_path: Path) -> None:
    store = RecipeStore(tmp_path)
    store.save(recipe())
    payload = recipe().model_dump(mode="json")
    payload["revision"] = "8"
    payload["effective_from"] = "2026-07-01T00:00:00+00:00"
    payload["approval_ref"] = "SIMULATED-VALIDATION-REF-HTST-008"
    store.save(ValidatedRecipe.model_validate(payload))
    selected = store.select_effective("HTST-01", datetime(2026, 8, 25, tzinfo=timezone.utc))
    assert selected.revision == "8"


def test_recipe_selection_uses_effective_revision(tmp_path: Path) -> None:
    store = RecipeStore(tmp_path)
    payload = recipe().model_dump(mode="json")
    payload["effective_to"] = "2026-07-01T00:00:00+00:00"
    r7 = ValidatedRecipe.model_validate(payload)
    store.save(r7)
    payload2 = recipe().model_dump(mode="json")
    payload2["revision"] = "8"
    payload2["effective_from"] = "2026-07-01T00:00:00+00:00"
    payload2["approval_ref"] = "SIMULATED-VALIDATION-REF-HTST-008"
    r8 = ValidatedRecipe.model_validate(payload2)
    store.save(r8)
    selected = store.select_effective("HTST-01", datetime(2026, 8, 25, tzinfo=timezone.utc))
    assert selected.revision == "8"


def test_recipe_values_are_versioned_and_have_approval_reference() -> None:
    r = recipe()
    assert r.revision == "7"
    assert r.approval_ref
    assert r.metadata["demo_only"] is True


def test_compliance_service_persists_immutable_idempotent_artifact(tmp_path: Path) -> None:
    from app.compliance.service import ComplianceService
    from app.reconstruction.service import ReconstructionService

    ingestion_id = "ing-compliance"
    points = scenario_points(tmp_path, "normal")
    normalized = tmp_path / "normalized" / ingestion_id
    normalized.mkdir(parents=True)
    records = []
    for p in points:
        common = {"ts_utc": p.ts.isoformat(), "asset": p.asset, "quality_code": "GOOD"}
        records.extend([
            {**common, "concept": "cip.return.temperature", "value_double": p.return_temperature_c, "value_text": None},
            {**common, "concept": "cip.return.flow", "value_double": p.return_flow_lpm, "value_text": None},
            {**common, "concept": "cip.return.conductivity", "value_double": p.return_conductivity_mscm, "value_text": None},
            {**common, "concept": "cip.return.pressure", "value_double": p.return_pressure_bar, "value_text": None},
            {**common, "concept": "cip.sequence.phase", "value_double": None, "value_text": p.explicit_phase},
        ])
    (normalized / "records.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")

    ReconstructionService(tmp_path).reconstruct_ingestion(ingestion_id)
    service = ComplianceService(tmp_path)
    service.save_recipe(recipe())
    first = service.evaluate_ingestion(ingestion_id)
    second = service.evaluate_ingestion(ingestion_id)
    assert first["overall_assessment"] == "COMPLIANT"
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["artifact_path"] == first["artifact_path"]
    assert first["lineage"]["recipe_sha256"]


def test_api_exposes_deterministic_compliance_demo() -> None:
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    response = client.get("/v1/demo/compliance/low_temp")
    assert response.status_code == 200
    body = response.json()
    assert body["overall_assessment"] == "PROCESS_DEVIATION"
    assert body["engine"] == "validated-cip-compliance"


def test_long_rinse_can_have_legitimate_stable_endpoint_without_being_called_sensor_freeze(tmp_path: Path) -> None:
    points = scenario_points(tmp_path, "excessive_rinse")
    result = evaluate_cycle(reconstruct_one(points), points, recipe())
    f = finding(result, "HTST-FINAL-RINSE-ENDPOINT")
    assert result["overall_assessment"] == "COMPLIANT"
    assert f["status"] == "PASS"
    assert any(x.get("accepted_as_stable_endpoint") is True for x in f["evidence"]["flatline_flags"])


def test_endpoint_flatline_from_phase_start_is_still_withheld_as_suspicious(tmp_path: Path) -> None:
    points = scenario_points(tmp_path, "normal")
    damaged = [
        replace(p, return_conductivity_mscm=0.8) if p.explicit_phase == "FINAL_RINSE" else p
        for p in points
    ]
    result = evaluate_cycle(reconstruct_one(damaged), damaged, recipe())
    f = finding(result, "HTST-FINAL-RINSE-ENDPOINT")
    assert f["status"] == "NOT_EVALUABLE"
    assert result["overall_assessment"] == "DATA_REVIEW_REQUIRED"


def test_missing_evidence_that_could_change_exposure_result_is_unknown_not_failure(tmp_path: Path) -> None:
    base = recipe()
    payload = base.model_dump(mode="json")
    for req in payload["requirements"]:
        if req["code"] == "HTST-CAUSTIC-VALIDATED-EXPOSURE":
            req["minimum_seconds"] = 1320
            req["minimum_data_coverage"] = 0.95
    strict = ValidatedRecipe.model_validate(payload)

    points = scenario_points(tmp_path, "normal")
    damaged = list(points)
    idx = next(i for i, p in enumerate(damaged) if p.explicit_phase == "CAUSTIC") + 30
    damaged[idx] = replace(damaged[idx], return_flow_lpm=None)
    result = evaluate_cycle(reconstruct_one(damaged), damaged, strict)
    f = finding(result, "HTST-CAUSTIC-VALIDATED-EXPOSURE")
    assert f["status"] == "NOT_EVALUABLE"
    assert result["overall_assessment"] == "DATA_REVIEW_REQUIRED"
