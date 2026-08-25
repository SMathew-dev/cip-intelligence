from __future__ import annotations

import csv
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from app.reconstruction.engine import canonicalize_phase, reconstruct_cycles
from app.reconstruction.io import normalized_records_to_points
from app.reconstruction.models import SignalPoint
from app.simulator import generate_cycle


@pytest.fixture()
def explicit_points(tmp_path: Path) -> list[SignalPoint]:
    path = generate_cycle(tmp_path / "cycle.csv", scenario="normal")
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


def test_phase_aliases_are_canonicalized() -> None:
    assert canonicalize_phase("alkali") == "CAUSTIC"
    assert canonicalize_phase("Rinse 1") == "INTERMEDIATE_RINSE"
    assert canonicalize_phase("final-water-rinse") == "FINAL_RINSE"
    assert canonicalize_phase("idle") == "INACTIVE"
    assert canonicalize_phase("mystery step") is None


def test_explicit_cycle_reconstruction_is_complete(explicit_points: list[SignalPoint]) -> None:
    result = reconstruct_cycles(explicit_points)
    assert result["cycle_count"] == 1
    cycle = result["cycles"][0]
    assert cycle["reconstruction_mode"] == "EXPLICIT"
    assert cycle["completeness"] == "COMPLETE"
    assert cycle["confidence"] >= 0.99
    assert [p["phase"] for p in cycle["phases"]] == [
        "PRE_RINSE", "CAUSTIC", "INTERMEDIATE_RINSE", "ACID", "FINAL_RINSE"
    ]
    assert [round(p["duration_seconds"] / 60) for p in cycle["phases"]] == [8, 22, 7, 10, 9]


def test_short_explicit_phase_glitch_is_repaired(explicit_points: list[SignalPoint]) -> None:
    points = list(explicit_points)
    # One false phase-label sample in the middle of CAUSTIC should not create a fake phase.
    idx = next(i for i, p in enumerate(points) if p.explicit_phase == "CAUSTIC") + 20
    points[idx] = replace(points[idx], explicit_phase="ACID")
    result = reconstruct_cycles(points)
    phases = [p["phase"] for p in result["cycles"][0]["phases"]]
    assert phases == ["PRE_RINSE", "CAUSTIC", "INTERMEDIATE_RINSE", "ACID", "FINAL_RINSE"]
    assert any(i["code"] == "EXPLICIT_PHASE_GLITCH_REPAIRED" for i in result["issues"])


def test_explicit_phase_reset_splits_two_cycles(explicit_points: list[SignalPoint]) -> None:
    shift = timedelta(hours=2)
    second = [replace(p, ts=p.ts + shift) for p in explicit_points]
    result = reconstruct_cycles(explicit_points + second)
    assert result["cycle_count"] == 2
    assert all(c["completeness"] == "COMPLETE" for c in result["cycles"])


def test_signal_only_reconstruction_is_labeled_inferred(explicit_points: list[SignalPoint]) -> None:
    unlabeled = [replace(p, explicit_phase=None) for p in explicit_points]
    result = reconstruct_cycles(unlabeled)
    assert result["cycle_count"] == 1
    cycle = result["cycles"][0]
    assert cycle["reconstruction_mode"] == "INFERRED"
    assert cycle["completeness"] == "COMPLETE"
    assert 0.75 <= cycle["confidence"] < 0.95
    assert [p["phase"] for p in cycle["phases"]] == [
        "PRE_RINSE", "CAUSTIC", "INTERMEDIATE_RINSE", "ACID", "FINAL_RINSE"
    ]


def test_signal_only_reconstruction_splits_on_large_gap(explicit_points: list[SignalPoint]) -> None:
    unlabeled = [replace(p, explicit_phase=None) for p in explicit_points]
    shift = timedelta(hours=2)
    second = [replace(p, ts=p.ts + shift) for p in unlabeled]
    result = reconstruct_cycles(unlabeled + second)
    assert result["cycle_count"] == 2


def test_inference_refuses_to_hallucinate_without_conductivity(explicit_points: list[SignalPoint]) -> None:
    weak = [replace(p, explicit_phase=None, return_conductivity_mscm=None) for p in explicit_points]
    result = reconstruct_cycles(weak)
    assert result["cycle_count"] == 0
    assert any(i["code"] == "INSUFFICIENT_PHASE_EVIDENCE" for i in result["issues"])


def test_duplicate_timestamp_level_points_are_rejected(explicit_points: list[SignalPoint]) -> None:
    corrupted = list(explicit_points)
    corrupted.insert(20, corrupted[19])
    result = reconstruct_cycles(corrupted)
    assert result["cycle_count"] == 0
    assert any(i["code"] == "DUPLICATE_TIMESTAMPS" for i in result["issues"])


def test_multiple_assets_are_reconstructed_independently(explicit_points: list[SignalPoint]) -> None:
    second = [replace(p, asset="HTST-02") for p in explicit_points]
    result = reconstruct_cycles(explicit_points + second)
    assert result["cycle_count"] == 2
    assert {c["asset"] for c in result["cycles"]} == {"HTST-01", "HTST-02"}


def test_redundant_disagreeing_normalized_sensors_are_withheld() -> None:
    records = [
        {"ts_utc": "2026-08-25T11:00:00+00:00", "asset": "HTST-01", "concept": "cip.return.temperature", "value_double": 72.0, "value_text": None, "quality_code": "GOOD"},
        {"ts_utc": "2026-08-25T11:00:00+00:00", "asset": "HTST-01", "concept": "cip.return.temperature", "value_double": 78.0, "value_text": None, "quality_code": "GOOD"},
        {"ts_utc": "2026-08-25T11:00:00+00:00", "asset": "HTST-01", "concept": "cip.return.flow", "value_double": 420.0, "value_text": None, "quality_code": "GOOD"},
        {"ts_utc": "2026-08-25T11:00:00+00:00", "asset": "HTST-01", "concept": "cip.sequence.phase", "value_double": None, "value_text": "CAUSTIC", "quality_code": "GOOD"},
    ]
    points = normalized_records_to_points(records)
    assert len(points) == 1
    assert points[0].return_temperature_c is None
    assert points[0].quality["cip.return.temperature"] == "REDUNDANT"


def test_metrics_are_calculated_per_phase(explicit_points: list[SignalPoint]) -> None:
    result = reconstruct_cycles(explicit_points)
    caustic = next(p for p in result["cycles"][0]["phases"] if p["phase"] == "CAUSTIC")
    assert caustic["metrics"]["return_temperature_c"]["mean"] == pytest.approx(74.5, abs=0.2)
    assert caustic["metrics"]["return_flow_lpm"]["samples"] == 22 * 6


def test_reconstruction_service_persists_versioned_idempotent_artifact(tmp_path: Path, explicit_points: list[SignalPoint]) -> None:
    import json
    from app.reconstruction.service import ReconstructionService

    ingestion_id = "ing-test"
    normalized = tmp_path / "normalized" / ingestion_id
    normalized.mkdir(parents=True)
    records = []
    for p in explicit_points:
        common = {"ts_utc": p.ts.isoformat(), "asset": p.asset, "quality_code": "GOOD"}
        records.extend([
            {**common, "concept": "cip.return.temperature", "value_double": p.return_temperature_c, "value_text": None},
            {**common, "concept": "cip.return.flow", "value_double": p.return_flow_lpm, "value_text": None},
            {**common, "concept": "cip.return.conductivity", "value_double": p.return_conductivity_mscm, "value_text": None},
            {**common, "concept": "cip.return.pressure", "value_double": p.return_pressure_bar, "value_text": None},
            {**common, "concept": "cip.sequence.phase", "value_double": None, "value_text": p.explicit_phase},
        ])
    (normalized / "records.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")

    service = ReconstructionService(tmp_path)
    first = service.reconstruct_ingestion(ingestion_id)
    second = service.reconstruct_ingestion(ingestion_id)
    assert first["result"]["cycle_count"] == 1
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["artifact_path"] == first["artifact_path"]
