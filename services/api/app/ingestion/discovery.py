from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from statistics import median
from typing import Iterable

from app.ingestion.semantic_registry import infer_unit, normalize_text


_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %I:%M:%S %p",
    "%d/%m/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%m/%d/%Y %H:%M:%S.%f",
)
_TIMESTAMP_HEADER_HINTS = {
    "timestamp", "date time", "datetime", "date_time", "time stamp",
    "event time", "sample time", "recorded at", "record time", "real time", "ts",
}


def _parse_datetime_candidate(raw: str) -> datetime | None:
    text = str(raw).strip()
    if not text:
        return None

    # Epoch seconds/milliseconds are accepted only in realistic absolute-time ranges.
    try:
        epoch = float(text)
        if math.isfinite(epoch) and 1e8 <= epoch <= 1e14:
            if epoch > 1e11:
                epoch /= 1000.0
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        pass

    candidate = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        pass
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _comparable_timestamp(value: datetime) -> float:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).timestamp()
    # For discovery only, naive local timestamps can still establish chronology.
    return value.replace(tzinfo=timezone.utc).timestamp()


def profile_values(values: Iterable[str]) -> dict:
    sample = [str(v).strip() for v in values if str(v).strip()][:100]
    numeric: list[float] = []
    for raw in sample:
        try:
            value = float(raw)
            if math.isfinite(value):
                numeric.append(value)
        except ValueError:
            continue
    result = {
        "sample_count": len(sample),
        "distinct_count": len(set(sample)),
        "numeric_fraction": round(len(numeric) / len(sample), 3) if sample else 0.0,
    }
    if numeric:
        result.update({
            "numeric_min": min(numeric),
            "numeric_max": max(numeric),
            "numeric_median": median(numeric),
        })
    return result


def timestamp_evidence(header: str, values: Iterable[str]) -> dict:
    sample = [str(v).strip() for v in values if str(v).strip()][:100]
    parsed = [_parse_datetime_candidate(v) for v in sample]
    usable = [p for p in parsed if p is not None]
    parse_fraction = len(usable) / len(sample) if sample else 0.0

    comparable = [_comparable_timestamp(p) for p in usable]
    monotonic_pairs = [b >= a for a, b in zip(comparable, comparable[1:])]
    monotonic_fraction = (
        sum(monotonic_pairs) / len(monotonic_pairs) if monotonic_pairs else (1.0 if usable else 0.0)
    )
    unique_fraction = len(set(comparable)) / len(comparable) if comparable else 0.0

    normalized = normalize_text(header)
    header_hint = 0.0
    if normalized in {normalize_text(x) for x in _TIMESTAMP_HEADER_HINTS}:
        header_hint = 1.0
    elif "time" in normalized or "date" in normalized:
        header_hint = 0.72

    # Values must carry most of the score; a column called "time" is not enough.
    confidence = 0.0
    if parse_fraction >= 0.8:
        confidence = 0.62 * parse_fraction + 0.23 * monotonic_fraction + 0.10 * unique_fraction + 0.05 * header_hint
        if header_hint >= 0.7:
            confidence = min(0.99, confidence + 0.04)
    elif header_hint == 1.0 and parse_fraction >= 0.5:
        confidence = 0.55 * parse_fraction + 0.20 * monotonic_fraction + 0.10 * unique_fraction + 0.15

    confidence = round(min(max(confidence, 0.0), 0.99), 3)
    reasons: list[str] = []
    if header_hint:
        reasons.append("time/date header cue")
    if parse_fraction:
        reasons.append(f"{round(parse_fraction * 100)}% datetime-like values")
    if usable:
        reasons.append(f"{round(monotonic_fraction * 100)}% chronological order")
    return {
        "column": header,
        "confidence": confidence,
        "parse_fraction": round(parse_fraction, 3),
        "monotonic_fraction": round(monotonic_fraction, 3),
        "unique_fraction": round(unique_fraction, 3),
        "reason": "; ".join(reasons) or "no timestamp evidence",
    }


def discover_timestamp_candidates(headers: list[str], rows: list[dict[str, str]]) -> list[dict]:
    candidates = [timestamp_evidence(h, (row.get(h, "") for row in rows)) for h in headers]
    candidates = [c for c in candidates if c["confidence"] >= 0.55]
    return sorted(candidates, key=lambda c: (-c["confidence"], c["column"]))[:5]


def infer_generic_measurement(header: str, *, numeric_fraction: float) -> dict | None:
    """Identify a measurement family without inventing CIP direction or equipment.

    A generic ``Temperature (C)`` is useful evidence about the source schema, but it
    is not automatically a CIP return temperature. The engineer must supply the
    missing process context before it can become an approved semantic mapping.
    """
    norm = normalize_text(header)
    tokens = set(norm.split())
    rules = (
        ("temperature", {"temperature", "temp"}, "C"),
        ("flow", {"flow", "flowrate"}, "L/min"),
        ("conductivity", {"conductivity", "cond"}, "mS/cm"),
        ("pressure", {"pressure", "press"}, "bar"),
        ("ph", {"ph"}, None),
        ("level", {"level"}, None),
        ("dissolved_oxygen", {"oxygen", "do"}, None),
    )
    for measurement_type, cues, canonical_unit in rules:
        if not (tokens & cues):
            continue
        source_unit = infer_unit(header)
        confidence = 0.91
        if source_unit:
            confidence += 0.05
        if numeric_fraction < 0.8:
            confidence -= 0.12
        return {
            "measurement_type": measurement_type,
            "canonical_unit": canonical_unit,
            "source_unit_guess": source_unit,
            "confidence": round(max(0.5, min(confidence, 0.98)), 3),
            "requires_context": True,
            "reason": (
                f"Recognizable {measurement_type.replace('_', ' ')} measurement cue"
                + (f" with explicit {source_unit} unit" if source_unit else "")
                + "; equipment/direction remains unconfirmed"
            ),
        }
    return None
