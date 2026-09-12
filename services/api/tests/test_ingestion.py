from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

from app.ingestion.csv_ingest import inspect_csv, normalize_csv, persist_ingestion
from app.ingestion.models import MappingField, MappingProfile
from app.ingestion.semantic_registry import infer_concept
from app.ingestion.service import IngestionService


@pytest.mark.parametrize("raw", ["NaN", "nan", "inf", "-Infinity", "1e999"])
def test_non_finite_sensor_values_are_withheld(raw: str) -> None:
    profile = MappingProfile(
        name="nonfinite", plant="Demo", source_system="Historian",
        timestamp_column="ts", asset_default="HTST-01",
        mappings=[MappingField(source_column="temp", concept="cip.return.temperature", source_unit="C")],
    )
    result = normalize_csv(f"ts,temp\n2026-08-25T11:00:00Z,{raw}\n".encode(), profile)
    record = result["records"][0]
    assert record["value_double"] is None
    assert record["value_text"] == raw
    assert record["quality_code"] == "NON_FINITE_VALUE"
    assert result["good_points"] == 0
    assert result["data_coverage"] == 0
    assert result["high_severity_issue_count"] == 1
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("field", ["scale_factor", "offset_value"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_mapping_rejects_non_finite_calibration(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        MappingField(source_column="temp", concept="cip.return.temperature", **{field: value})


def test_non_finite_evidence_stays_withheld_after_persistence(tmp_path: Path) -> None:
    from app.reconstruction.io import load_normalized_jsonl

    profile = MappingProfile(
        name="persist-nonfinite", plant="Demo", source_system="Historian",
        timestamp_column="ts", asset_default="HTST-01",
        mappings=[MappingField(source_column="temp", concept="cip.return.temperature", source_unit="C")],
    )
    content = b"ts,temp\n2026-08-25T11:00:00Z,NaN\n2026-08-25T11:00:10Z,80\n"
    result = normalize_csv(content, profile)
    saved = persist_ingestion(content, "sensor.csv", result, tmp_path / "raw", tmp_path / "normalized")
    assert Path(saved["raw_object"]).read_bytes() == content
    records_path = Path(saved["normalized_object"])
    records = [json.loads(line) for line in records_path.read_text().splitlines()]
    json.dumps(records, allow_nan=False)
    points = load_normalized_jsonl(records_path)
    assert points[0].return_temperature_c is None
    assert points[1].return_temperature_c == 80
    assert result["data_coverage"] == 0.5


@pytest.mark.parametrize("unit,scale,offset", [("C", 1e308, 0), ("C", 1, 1e308), ("gpm", 1, 0)])
def test_numeric_overflow_is_withheld(unit: str, scale: float, offset: float) -> None:
    profile = MappingProfile(
        name="overflow", plant="Demo", source_system="Historian", timestamp_column="ts",
        mappings=[MappingField(
            source_column="value", concept="cip.return.flow" if unit == "gpm" else "cip.return.temperature",
            source_unit=unit, scale_factor=scale, offset_value=offset,
        )],
    )
    result = normalize_csv(b"ts,value\n2026-08-25T11:00:00Z,1.79e308\n", profile)
    assert result["records"][0]["quality_code"] == "NON_FINITE_VALUE"
    assert result["records"][0]["value_double"] is None
    json.dumps(result, allow_nan=False)


@pytest.fixture()
def messy_content() -> bytes:
    root = Path(__file__).resolve().parents[3]
    return (root / "data" / "messy_historian_export.csv").read_bytes()


@pytest.fixture()
def profile() -> MappingProfile:
    root = Path(__file__).resolve().parents[3]
    return MappingProfile.model_validate_json((root / "config" / "messy_demo_mapping.json").read_text())


def test_opaque_instrument_id_is_not_directionally_guessed() -> None:
    assert infer_concept("FIT_214") == []
    candidates = infer_concept("FIT_214_RET")
    assert candidates[0]["concept"] == "cip.return.flow"


def test_inspection_sniffs_legacy_semicolon_and_proposes_safe_mappings(messy_content: bytes) -> None:
    result = inspect_csv(messy_content)
    assert result["delimiter"] == ";"
    assert result["timestamp_candidate"]["column"] == "Date/Time Local"
    columns = {x["source_column"]: x for x in result["columns"]}
    assert columns["CIP Return Temp [F]"]["mapping_candidates"][0]["concept"] == "cip.return.temperature"
    assert columns["CIP Return Temp [F]"]["mapping_candidates"][0]["source_unit_guess"] == "F"
    assert columns["CIP Return Flow [gpm]"]["mapping_candidates"][0]["concept"] == "cip.return.flow"
    assert columns["RET_COND [uS/cm]"]["mapping_candidates"][0]["concept"] == "cip.return.conductivity"


def test_inspection_organizes_row_oriented_export_by_explicit_circuit() -> None:
    content = (
        "ts,Circuit,Return Temp [F],Return Flow [gpm]\n"
        "2026-08-25T11:00:00Z,POL1,120,100\n"
        "2026-08-25T11:00:10Z,POL2,121,101\n"
        "2026-08-25T11:00:20Z,POL1,122,102\n"
    ).encode()
    proposal = inspect_csv(content)["organization_proposal"]
    assert proposal["mode"] == "row_identity"
    assert proposal["source_column"] == "Circuit"
    assert [a["proposed_name"] for a in proposal["assets"]] == ["POL1", "POL2"]
    assert "Return Temp [F]" in proposal["assets"][0]["signal_columns"]
    assert proposal["review_required"] is True


def test_inspection_organizes_wide_export_from_equipment_tag_prefixes() -> None:
    content = (
        "ts,POL1 Return Temp [F],POL1 Return Flow [gpm],POL2 Return Temp [F],POL2 Return Flow [gpm]\n"
        "2026-08-25T11:00:00Z,120,100,121,99\n"
    ).encode()
    proposal = inspect_csv(content)["organization_proposal"]
    assert proposal["mode"] == "wide_tag_prefix"
    assert [a["proposed_name"] for a in proposal["assets"]] == ["POL1", "POL2"]
    assert proposal["assets"][0]["signal_columns"] == [
        "POL1 Return Temp [F]", "POL1 Return Flow [gpm]"
    ]


def test_inspection_does_not_invent_equipment_identity() -> None:
    result = inspect_csv(b"ts,Return Temp [F]\n2026-08-25T11:00:00Z,120\n")
    assert result["organization_proposal"]["mode"] == "unresolved"
    assert result["organization_proposal"]["assets"] == []


def test_normalization_converts_units_and_local_time_to_utc(messy_content: bytes, profile: MappingProfile) -> None:
    result = normalize_csv(messy_content, profile)
    assert result["rows_in_source"] == 8
    assert result["normalized_points"] == 40
    assert result["data_coverage"] == 1.0
    assert result["high_severity_issue_count"] == 0

    records = result["records"]
    first_temp = next(r for r in records if r["concept"] == "cip.return.temperature")
    assert first_temp["asset"] == "HTST-01"
    assert first_temp["ts_utc"] == "2026-08-25T11:00:00+00:00"
    assert first_temp["value_double"] == pytest.approx(29.0, abs=0.01)

    first_flow = next(r for r in records if r["concept"] == "cip.return.flow")
    assert first_flow["value_double"] == pytest.approx(414.9, abs=0.2)

    first_cond = next(r for r in records if r["concept"] == "cip.return.conductivity")
    assert first_cond["value_double"] == pytest.approx(17.8, abs=0.001)

    first_pressure = next(r for r in records if r["concept"] == "cip.return.pressure")
    assert first_pressure["value_double"] == pytest.approx(2.84, abs=0.02)


def test_mapping_requires_explicit_engineering_units(tmp_path: Path) -> None:
    service = IngestionService(tmp_path)
    unsafe = MappingProfile(
        name="unsafe",
        plant="Demo",
        source_system="Historian",
        timezone="UTC",
        timestamp_column="ts",
        mappings=[MappingField(source_column="TT1", concept="cip.return.temperature")],
    )
    with pytest.raises(ValueError, match="will not silently assume engineering units"):
        service.save_mapping(unsafe)


def test_sampling_gap_is_reported() -> None:
    content = (
        "ts,ret_flow_lpm\n"
        "2026-08-25T11:00:00Z,400\n"
        "2026-08-25T11:00:10Z,401\n"
        "2026-08-25T11:00:20Z,402\n"
        "2026-08-25T11:03:20Z,403\n"
    ).encode()
    profile = MappingProfile(
        name="gap",
        plant="Demo",
        source_system="Historian",
        timezone="UTC",
        timestamp_column="ts",
        asset_default="HTST-01",
        mappings=[MappingField(source_column="ret_flow_lpm", concept="cip.return.flow", source_unit="L/min")],
    )
    result = normalize_csv(content, profile)
    assert any(i["code"] == "SAMPLING_GAP" for i in result["issues"])


def test_raw_ingestion_preserves_exact_bytes_and_checksum(tmp_path: Path, messy_content: bytes, profile: MappingProfile) -> None:
    result = normalize_csv(messy_content, profile)
    persisted = persist_ingestion(messy_content, "legacy.csv", result, tmp_path / "raw", tmp_path / "normalized")
    raw_path = Path(persisted["raw_object"])
    assert raw_path.read_bytes() == messy_content
    assert persisted["sha256"] == hashlib.sha256(messy_content).hexdigest()
    assert not (raw_path.stat().st_mode & stat.S_IWUSR)

    summary_path = Path(persisted["normalized_object"]).parent / "summary.json"
    summary = json.loads(summary_path.read_text())
    assert "records" not in summary
    assert summary["data_coverage"] == 1.0


def test_ambiguous_dst_local_time_is_rejected() -> None:
    from app.ingestion.csv_ingest import parse_timestamp
    with pytest.raises(ValueError, match="ambiguous"):
        parse_timestamp("11/01/2026 01:30:00", "America/Chicago")


def test_duplicate_file_ingestion_is_idempotent(tmp_path: Path, messy_content: bytes, profile: MappingProfile) -> None:
    result = normalize_csv(messy_content, profile)
    first = persist_ingestion(messy_content, "legacy.csv", result, tmp_path / "raw", tmp_path / "normalized")
    second = persist_ingestion(messy_content, "legacy.csv", result, tmp_path / "raw", tmp_path / "normalized")
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["ingestion_id"] == first["ingestion_id"]
