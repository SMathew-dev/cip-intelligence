from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from app.acquisition.adapters.watched_folder import WatchedFolderAdapter
from app.acquisition.models import AcquisitionSource
from app.acquisition.service import AcquisitionService, UnsupportedAdapterError
from app.ingestion.models import MappingField, MappingProfile
from app.ingestion.service import IngestionService


def _profile(name: str = "auto") -> MappingProfile:
    return MappingProfile(
        name=name,
        plant="Demo Dairy",
        source_system="Legacy Historian Export",
        timezone="UTC",
        timestamp_column="ts",
        asset_default="HTST-01",
        mappings=[
            MappingField(source_column="return_temp_f", concept="cip.return.temperature", source_unit="F"),
            MappingField(source_column="return_flow_gpm", concept="cip.return.flow", source_unit="gpm"),
        ],
    )


def _csv(temp_f: float = 167.0) -> bytes:
    return (
        "ts,return_temp_f,return_flow_gpm\n"
        f"2026-08-25T11:00:00Z,{temp_f},110\n"
        f"2026-08-25T11:00:10Z,{temp_f + 0.2},111\n"
        f"2026-08-25T11:00:20Z,{temp_f + 0.4},112\n"
        f"2026-08-25T11:00:30Z,{temp_f + 0.6},113\n"
    ).encode()


def test_acquisition_source_refuses_write_capability(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be read-only"):
        AcquisitionSource(
            name="bad",
            adapter_type="watched_folder",
            mapping_profile="auto",
            read_only=False,
            config={"folder": str(tmp_path)},
        )


def test_watched_folder_ignores_unsettled_and_temp_files(tmp_path: Path) -> None:
    stable = tmp_path / "stable.csv"
    fresh = tmp_path / "fresh.csv"
    temp = tmp_path / "ignored.tmp"
    stable.write_bytes(_csv())
    fresh.write_bytes(_csv(168))
    temp.write_bytes(_csv(169))
    old = time.time() - 120
    os.utime(stable, (old, old))
    source = AcquisitionSource(
        name="folder",
        adapter_type="watched_folder",
        mapping_profile="auto",
        config={"folder": str(tmp_path), "patterns": ["*.csv", "*.tmp"], "settle_seconds": 30},
    )
    found = WatchedFolderAdapter(source).discover()
    assert [c.filename for c in found] == ["stable.csv"]


def test_watched_folder_acquisition_is_automatic_and_idempotent(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    ingestion = IngestionService(runtime)
    ingestion.save_mapping(_profile())
    acquisition = AcquisitionService(runtime, ingestion_service=ingestion)
    acquisition.save_source(AcquisitionSource(
        name="hist-export",
        adapter_type="watched_folder",
        mapping_profile="auto",
        config={"folder": str(incoming), "patterns": ["*.csv"], "settle_seconds": 0},
    ))

    export = incoming / "HTST_20260825.csv"
    export.write_bytes(_csv())
    first = acquisition.run_source("hist-export")
    assert first["processed"] == 1
    assert first["jobs"][0]["status"] == "SUCCEEDED"
    ingestion_id = first["jobs"][0]["ingestion_id"]
    assert ingestion_id

    second = acquisition.run_source("hist-export")
    assert second["processed"] == 0

    # A copied file with identical bytes has a different source ref, but the
    # ingestion layer still deduplicates the same bytes+mapping context.
    copy = incoming / "HTST_20260825_copy.csv"
    copy.write_bytes(_csv())
    third = acquisition.run_source("hist-export")
    assert third["processed"] == 1
    assert third["jobs"][0]["status"] == "SUCCEEDED"  # source identity is part of lineage/context
    assert third["jobs"][0]["duplicate"] is False


def test_same_bytes_under_same_source_ref_and_mapping_are_idempotent(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    ingestion = IngestionService(runtime)
    ingestion.save_mapping(_profile())
    acquisition = AcquisitionService(runtime, ingestion_service=ingestion)
    acquisition.save_source(AcquisitionSource(
        name="hist-export",
        adapter_type="watched_folder",
        mapping_profile="auto",
        config={"folder": str(incoming), "settle_seconds": 0},
    ))
    export = incoming / "cycle.csv"
    export.write_bytes(_csv())
    first = acquisition.run_source("hist-export")
    assert first["jobs"][0]["status"] == "SUCCEEDED"

    # Force reprocessing of the same source object: persistence should return
    # the original ingestion rather than create duplicate analytical data.
    second = acquisition.run_source("hist-export", include_seen=True)
    assert second["jobs"][0]["status"] == "SKIPPED"
    assert second["jobs"][0]["duplicate"] is True
    assert second["jobs"][0]["ingestion_id"] == first["jobs"][0]["ingestion_id"]


def test_same_bytes_with_different_mapping_context_are_not_reused(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    ingestion = IngestionService(runtime)
    p1 = _profile("map-a")
    p2 = _profile("map-b")
    # Same source columns and bytes, but a changed calibration offset is a
    # materially different normalization context.
    p2.mappings[0].offset_value = 1.0
    ingestion.save_mapping(p1)
    ingestion.save_mapping(p2)
    content = _csv()
    a = ingestion.ingest(content, "same.csv", "map-a", source_identity="plant-source")
    b = ingestion.ingest(content, "same.csv", "map-b", source_identity="plant-source")
    assert a["duplicate"] is False
    assert b["duplicate"] is False
    assert a["ingestion_id"] != b["ingestion_id"]
    assert a["normalization_context_sha256"] != b["normalization_context_sha256"]


def test_unimplemented_industrial_adapter_is_explicit_not_fake(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    ingestion = IngestionService(runtime)
    ingestion.save_mapping(_profile())
    acquisition = AcquisitionService(runtime, ingestion_service=ingestion)
    acquisition.save_source(AcquisitionSource(
        name="opc-prod",
        adapter_type="opcua",
        mapping_profile="auto",
        config={"endpoint": "opc.tcp://example.invalid:4840"},
    ))
    with pytest.raises(UnsupportedAdapterError, match="not implemented"):
        acquisition.run_source("opc-prod")


def test_job_failure_is_persisted_and_retryable(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    ingestion = IngestionService(runtime)
    ingestion.save_mapping(_profile())
    acquisition = AcquisitionService(runtime, ingestion_service=ingestion)
    acquisition.save_source(AcquisitionSource(
        name="hist-export",
        adapter_type="watched_folder",
        mapping_profile="auto",
        config={"folder": str(incoming), "settle_seconds": 0},
    ))
    bad = incoming / "bad.csv"
    bad.write_text("wrong,headers\n1,2\n", encoding="utf-8")
    result = acquisition.run_source("hist-export")
    assert result["jobs"][0]["status"] == "FAILED"
    job_id = result["jobs"][0]["id"]
    persisted = acquisition.job_store.load(job_id)
    assert persisted.error

    bad.write_bytes(_csv())
    retry = acquisition.retry_job(job_id)
    assert retry["status"] == "SUCCEEDED"
    assert retry["metadata"]["retry_of"] == job_id
