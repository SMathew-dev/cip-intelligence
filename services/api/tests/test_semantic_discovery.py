from __future__ import annotations

from pathlib import Path

from app.ingestion.service import IngestionService


def test_real_time_is_detected_from_values_not_just_header(tmp_path: Path) -> None:
    content = (
        "Real Time,Temperature (C),Phase of Membrane\n"
        "2017-06-20 07:57:00,21.2,1\n"
        "2017-06-20 07:57:01,21.3,1\n"
        "2017-06-20 07:57:02,21.4,1\n"
        "2017-06-20 07:57:03,21.5,1\n"
    ).encode()
    result = IngestionService(tmp_path).inspect(content)

    assert result["timestamp_candidate"]["column"] == "Real Time"
    assert result["timestamp_candidate"]["value_supported"] is True
    assert result["timestamp_candidate"]["confidence"] >= 0.9
    assert result["inspection_version"] == "1.2-semantic-discovery"


def test_generic_temperature_is_recognized_without_guessing_cip_direction(tmp_path: Path) -> None:
    content = (
        "Real Time,Temperature (C)\n"
        "2017-06-20 07:57:00,21.2\n"
        "2017-06-20 07:57:01,21.3\n"
        "2017-06-20 07:57:02,21.4\n"
    ).encode()
    result = IngestionService(tmp_path).inspect(content)
    column = next(c for c in result["columns"] if c["source_column"] == "Temperature (C)")

    assert column["mapping_candidates"] == []
    assert column["measurement_candidate"]["measurement_type"] == "temperature"
    assert column["measurement_candidate"]["source_unit_guess"] == "C"
    assert column["measurement_candidate"]["requires_context"] is True
    assert column["measurement_candidate"]["confidence"] >= 0.9


def test_discovery_does_not_promote_arbitrary_text_to_timestamp(tmp_path: Path) -> None:
    content = (
        "Operator Note,Temperature (C)\n"
        "startup,20.1\n"
        "running,20.2\n"
        "stable,20.3\n"
    ).encode()
    result = IngestionService(tmp_path).inspect(content)

    assert result["timestamp_candidate"]["column"] is None
    assert result["timestamp_candidates"] == []


def test_value_profile_is_exposed_for_engineering_review(tmp_path: Path) -> None:
    content = (
        "Real Time,Pressure (bar)\n"
        "2017-06-20 07:57:00,1.0\n"
        "2017-06-20 07:57:01,1.5\n"
        "2017-06-20 07:57:02,2.0\n"
    ).encode()
    result = IngestionService(tmp_path).inspect(content)
    column = next(c for c in result["columns"] if c["source_column"] == "Pressure (bar)")

    assert column["value_profile"]["numeric_fraction"] == 1.0
    assert column["value_profile"]["numeric_min"] == 1.0
    assert column["value_profile"]["numeric_max"] == 2.0
    assert column["measurement_candidate"]["measurement_type"] == "pressure"
