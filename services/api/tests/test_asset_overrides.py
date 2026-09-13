from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ingestion.models import MappingField, MappingProfile
from app.ingestion.service import IngestionService


def _wide_profile() -> MappingProfile:
    return MappingProfile(
        name="wide-polishers",
        plant="Demo Dairy",
        source_system="Historian CSV",
        timezone="UTC",
        timestamp_column="ts",
        mappings=[
            MappingField(
                source_column="POL1 Return Temp [C]",
                concept="cip.return.temperature",
                source_unit="C",
                asset_override="POL1",
            ),
            MappingField(
                source_column="POL2 Return Temp [C]",
                concept="cip.return.temperature",
                source_unit="C",
                asset_override="POL2",
            ),
        ],
    )


def test_wide_historian_signals_remain_separated_by_confirmed_asset(tmp_path: Path) -> None:
    service = IngestionService(tmp_path)
    profile = _wide_profile()
    service.save_mapping(profile)
    content = (
        "ts,POL1 Return Temp [C],POL2 Return Temp [C]\n"
        "2026-08-25T11:00:00Z,70,75\n"
        "2026-08-25T11:00:10Z,71,76\n"
    ).encode()

    result = service.ingest(content, "wide.csv", profile.name)
    records = [
        json.loads(line)
        for line in Path(result["normalized_object"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert {(r["asset"], r["source_column"]) for r in records} == {
        ("POL1", "POL1 Return Temp [C]"),
        ("POL2", "POL2 Return Temp [C]"),
    }
    assert {r["asset"] for r in records if r["value_double"] == 70.0} == {"POL1"}
    assert {r["asset"] for r in records if r["value_double"] == 75.0} == {"POL2"}


def test_wide_mapping_requires_asset_assignment_for_every_signal(tmp_path: Path) -> None:
    service = IngestionService(tmp_path)
    profile = _wide_profile().model_copy(update={
        "name": "incomplete-wide",
        "mappings": [
            _wide_profile().mappings[0],
            _wide_profile().mappings[1].model_copy(update={"asset_override": None}),
        ],
    })
    service.save_mapping(profile)
    content = (
        "ts,POL1 Return Temp [C],POL2 Return Temp [C]\n"
        "2026-08-25T11:00:00Z,70,75\n"
    ).encode()

    with pytest.raises(ValueError, match="asset assignment for every mapped signal"):
        service.ingest(content, "wide.csv", profile.name)
