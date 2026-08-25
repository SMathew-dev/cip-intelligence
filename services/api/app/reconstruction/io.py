from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import SignalPoint


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ValueError("reconstruction requires timezone-aware timestamps")
    return ts


def normalized_records_to_points(records: Iterable[dict]) -> list[SignalPoint]:
    """Pivot normalized semantic records into timestamp-level signal points.

    Multiple physical sensors may map to the same semantic concept. This first
    reconstruction milestone only auto-collapses them when their GOOD values
    agree closely; materially disagreeing redundant instruments are withheld so
    later evidence reconciliation can decide which one to trust.
    """
    grouped: dict[tuple[str, str], dict[str, list[dict]]] = {}
    for record in records:
        asset = record.get("asset") or "UNKNOWN_ASSET"
        ts = record["ts_utc"]
        grouped.setdefault((asset, ts), {}).setdefault(record["concept"], []).append(record)

    def numeric(concept_records: list[dict] | None, tolerance: float) -> float | None:
        if not concept_records:
            return None
        vals = [
            float(r["value_double"])
            for r in concept_records
            if r.get("value_double") is not None and r.get("quality_code", "GOOD") == "GOOD"
        ]
        if not vals:
            return None
        if max(vals) - min(vals) > tolerance:
            return None
        return sum(vals) / len(vals)

    def text(concept_records: list[dict] | None) -> str | None:
        if not concept_records:
            return None
        vals = [
            str(r["value_text"]).strip()
            for r in concept_records
            if r.get("value_text") is not None and r.get("quality_code", "GOOD") == "GOOD"
        ]
        unique = {v for v in vals if v}
        if len(unique) != 1:
            return None
        return next(iter(unique))

    points: list[SignalPoint] = []
    for (asset, ts_text), by_concept in grouped.items():
        quality: dict[str, str] = {}
        for concept, recs in by_concept.items():
            if len(recs) > 1 and concept in {
                "cip.return.temperature",
                "cip.return.flow",
                "cip.return.conductivity",
                "cip.return.pressure",
            }:
                vals = [r.get("value_double") for r in recs if r.get("value_double") is not None]
                if len(vals) > 1:
                    quality[concept] = "REDUNDANT"

        points.append(SignalPoint(
            ts=_parse_ts(ts_text),
            asset=asset,
            return_temperature_c=numeric(by_concept.get("cip.return.temperature"), tolerance=1.0),
            return_flow_lpm=numeric(by_concept.get("cip.return.flow"), tolerance=15.0),
            return_conductivity_mscm=numeric(by_concept.get("cip.return.conductivity"), tolerance=3.0),
            return_pressure_bar=numeric(by_concept.get("cip.return.pressure"), tolerance=0.3),
            explicit_phase=text(by_concept.get("cip.sequence.phase")),
            quality=quality,
        ))

    return sorted(points, key=lambda p: (p.asset, p.ts))


def load_normalized_jsonl(path: Path) -> list[SignalPoint]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return normalized_records_to_points(records)
