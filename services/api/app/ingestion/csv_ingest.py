from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.ingestion.models import MappingProfile
from app.ingestion.semantic_registry import get_concept, infer_concept, infer_timestamp_column
from app.ingestion.units import convert_value


SUPPORTED_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252")


@dataclass(frozen=True)
class ParsedTable:
    headers: list[str]
    rows: list[dict[str, str]]
    delimiter: str
    encoding: str


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def decode_text(content: bytes) -> tuple[str, str]:
    for encoding in SUPPORTED_ENCODINGS:
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV encoding is not supported (expected UTF-8 or Windows-1252 compatible text).")


def parse_csv_bytes(content: bytes, max_rows: int | None = None) -> ParsedTable:
    if not content:
        raise ValueError("Uploaded CSV is empty.")
    text, encoding = decode_text(content)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("CSV does not contain a header row.")
    headers = [str(x).strip() for x in reader.fieldnames]
    if len(headers) != len(set(headers)):
        raise ValueError("CSV contains duplicate column names; rename them before mapping.")

    rows: list[dict[str, str]] = []
    for idx, row in enumerate(reader):
        if max_rows is not None and idx >= max_rows:
            break
        rows.append({str(k).strip(): ("" if v is None else str(v).strip()) for k, v in row.items()})
    if not rows:
        raise ValueError("CSV contains headers but no data rows.")
    return ParsedTable(headers=headers, rows=rows, delimiter=delimiter, encoding=encoding)


def inspect_csv(content: bytes, preview_rows: int = 8) -> dict:
    table = parse_csv_bytes(content, max_rows=250)
    timestamp_column, timestamp_confidence = infer_timestamp_column(table.headers)
    columns = []
    for header in table.headers:
        non_empty = [r.get(header, "") for r in table.rows if r.get(header, "") != ""]
        numeric_count = 0
        for value in non_empty[:100]:
            try:
                float(value)
                numeric_count += 1
            except ValueError:
                pass
        candidates = [] if header == timestamp_column else infer_concept(header)
        columns.append({
            "source_column": header,
            "non_empty_samples": len(non_empty),
            "numeric_fraction": round(numeric_count / max(min(len(non_empty), 100), 1), 3),
            "mapping_candidates": candidates,
        })

    return {
        "sha256": sha256_bytes(content),
        "encoding": table.encoding,
        "delimiter": table.delimiter,
        "row_count_previewed": len(table.rows),
        "timestamp_candidate": {
            "column": timestamp_column,
            "confidence": timestamp_confidence,
        },
        "columns": columns,
        "preview": table.rows[:preview_rows],
        "rule": "Mapping candidates are suggestions only; they are not plant-approved mappings until saved explicitly.",
    }


def parse_timestamp(raw: str, plant_timezone: str) -> datetime:
    text = raw.strip()
    if not text:
        raise ValueError("blank timestamp")

    # Numeric epoch seconds or milliseconds.
    try:
        epoch = float(text)
        if math.isfinite(epoch) and epoch > 1e8:
            if epoch > 1e11:
                epoch /= 1000.0
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except ValueError:
        pass

    dt: datetime | None = None
    candidate = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        formats = (
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %I:%M:%S %p",
            "%d/%m/%Y %H:%M:%S",
        )
        for fmt in formats:
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        raise ValueError(f"unsupported timestamp format: {raw!r}")

    if dt.tzinfo is None:
        zone = ZoneInfo(plant_timezone)
        valid: list[datetime] = []
        for fold in (0, 1):
            aware = dt.replace(tzinfo=zone, fold=fold)
            roundtrip = aware.astimezone(timezone.utc).astimezone(zone)
            if roundtrip.replace(tzinfo=None) == dt and roundtrip.fold == fold:
                valid.append(aware)
        unique_instants = {x.astimezone(timezone.utc) for x in valid}
        if not unique_instants:
            raise ValueError(
                f"local timestamp {raw!r} does not exist in timezone {plant_timezone!r} "
                "(likely a daylight-saving transition); provide an explicit UTC offset"
            )
        if len(unique_instants) > 1:
            raise ValueError(
                f"local timestamp {raw!r} is ambiguous in timezone {plant_timezone!r}; "
                "provide an explicit UTC offset or disambiguated source timestamp"
            )
        dt = valid[0]
    return dt.astimezone(timezone.utc)


def _normalize_scalar(raw: str, concept: str, source_unit: str | None, scale: float, offset: float) -> tuple[float | None, str | None, str | None]:
    semantic = get_concept(concept)
    if semantic is None:
        raise ValueError(f"Unknown semantic concept: {concept}")

    if raw == "":
        return None, None, "MISSING_VALUE"

    # Text/state concepts are retained as text.
    if semantic.canonical_unit is None and concept not in {"cip.return.ph"}:
        return None, raw, None

    try:
        numeric = float(raw)
    except ValueError:
        return None, raw, "NON_NUMERIC_VALUE"

    numeric = numeric * scale + offset
    try:
        canonical = convert_value(numeric, source_unit, semantic.canonical_unit)
    except ValueError:
        return numeric, None, "UNSUPPORTED_UNIT_CONVERSION"

    if semantic.plausible_range:
        low, high = semantic.plausible_range
        if canonical < low or canonical > high:
            return canonical, None, "OUTSIDE_PLAUSIBLE_RANGE"
    return canonical, None, None


def normalize_csv(content: bytes, profile: MappingProfile) -> dict:
    table = parse_csv_bytes(content)
    if profile.timestamp_column not in table.headers:
        raise ValueError(f"Timestamp column {profile.timestamp_column!r} is not present in CSV.")

    unknown_columns = [m.source_column for m in profile.mappings if m.source_column not in table.headers]
    if unknown_columns:
        raise ValueError(f"Mapped source columns missing from CSV: {unknown_columns}")

    issues: list[dict] = []
    normalized: list[dict] = []
    last_ts: datetime | None = None
    duplicate_keys: set[tuple[str, str, str, str | None]] = set()
    seen_keys: set[tuple[str, str, str, str | None]] = set()

    for row_number, row in enumerate(table.rows, start=2):
        try:
            ts = parse_timestamp(row.get(profile.timestamp_column, ""), profile.timezone)
        except Exception as exc:
            issues.append({"code": "INVALID_TIMESTAMP", "severity": "HIGH", "row": row_number, "detail": str(exc)})
            continue

        if last_ts is not None and ts < last_ts:
            issues.append({"code": "OUT_OF_ORDER_TIMESTAMP", "severity": "MEDIUM", "row": row_number, "detail": ts.isoformat()})
        last_ts = ts

        asset = profile.asset_default
        asset_mapping = next((m for m in profile.mappings if m.concept == "cip.asset"), None)
        if asset_mapping is not None:
            explicit_asset = row.get(asset_mapping.source_column, "").strip()
            if explicit_asset:
                asset = explicit_asset

        for mapping in profile.mappings:
            if mapping.concept == "cip.asset":
                continue
            raw = row.get(mapping.source_column, "")
            value_double, value_text, quality = _normalize_scalar(
                raw, mapping.concept, mapping.source_unit, mapping.scale_factor, mapping.offset_value
            )

            key = (mapping.source_column, mapping.concept, ts.isoformat(), asset)
            if key in seen_keys:
                duplicate_keys.add(key)
            seen_keys.add(key)

            if quality:
                issues.append({
                    "code": quality,
                    "severity": "HIGH" if quality in {"OUTSIDE_PLAUSIBLE_RANGE", "UNSUPPORTED_UNIT_CONVERSION"} else "MEDIUM",
                    "row": row_number,
                    "column": mapping.source_column,
                    "concept": mapping.concept,
                    "raw_value": raw,
                })

            semantic = get_concept(mapping.concept)
            normalized.append({
                "ts_utc": ts.isoformat(),
                "asset": asset,
                "source_column": mapping.source_column,
                "concept": mapping.concept,
                "value_double": value_double,
                "value_text": value_text,
                "canonical_unit": None if semantic is None else semantic.canonical_unit,
                "source_unit": mapping.source_unit,
                "quality_code": quality or "GOOD",
                "source_row": row_number,
            })

    if duplicate_keys:
        issues.append({
            "code": "DUPLICATE_SEMANTIC_TIMESTAMP",
            "severity": "HIGH",
            "count": len(duplicate_keys),
            "detail": "The same source tag repeats at the same concept/timestamp/asset key.",
        })

    # Sampling-gap detection is concept/asset-specific. We learn the typical interval
    # from the file rather than assuming a fixed historian scan rate.
    by_series: dict[tuple[str, str, str | None], list[datetime]] = {}
    for record in normalized:
        if record["quality_code"] == "GOOD":
            by_series.setdefault((record["source_column"], record["concept"], record["asset"]), []).append(
                datetime.fromisoformat(record["ts_utc"])
            )
    for (source_column, concept, asset), timestamps in by_series.items():
        timestamps = sorted(set(timestamps))
        if len(timestamps) < 4:
            continue
        deltas = [(b - a).total_seconds() for a, b in zip(timestamps, timestamps[1:]) if b > a]
        if not deltas:
            continue
        ordered = sorted(deltas)
        median_delta = ordered[len(ordered) // 2]
        if median_delta <= 0:
            continue
        threshold = max(median_delta * 3.0, median_delta + 30.0)
        large_gaps = [d for d in deltas if d > threshold]
        if large_gaps:
            issues.append({
                "code": "SAMPLING_GAP",
                "severity": "MEDIUM",
                "source_column": source_column,
                "concept": concept,
                "asset": asset,
                "median_interval_seconds": median_delta,
                "gap_count": len(large_gaps),
                "largest_gap_seconds": max(large_gaps),
            })

    total_points = len(normalized)
    good_points = sum(1 for r in normalized if r["quality_code"] == "GOOD")
    high_issues = sum(1 for i in issues if i["severity"] == "HIGH")

    return {
        "rows_in_source": len(table.rows),
        "normalized_points": total_points,
        "good_points": good_points,
        "data_coverage": round(good_points / total_points, 4) if total_points else 0.0,
        "high_severity_issue_count": high_issues,
        "issues": issues,
        "records": normalized,
    }


def persist_ingestion(
    content: bytes,
    original_filename: str,
    normalized_result: dict,
    raw_root: Path,
    normalized_root: Path,
    normalization_context: dict | None = None,
) -> dict:
    checksum = sha256_bytes(content)
    normalization_context = normalization_context or {}
    context_json = json.dumps(normalization_context, sort_keys=True, separators=(",", ":"), default=str)
    context_sha256 = hashlib.sha256(context_json.encode("utf-8")).hexdigest()
    # Idempotency matters for watched folders and scheduled exports. Re-seeing the
    # exact same bytes should not silently create a second analytical dataset.
    if raw_root.exists():
        for manifest_path in raw_root.glob("*/manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("sha256") == checksum and manifest.get("normalization_context_sha256") == context_sha256:
                existing_id = manifest.get("ingestion_id", manifest_path.parent.name)
                existing_normalized = normalized_root / existing_id / "records.jsonl"
                existing_summary_path = normalized_root / existing_id / "summary.json"
                existing_summary = (
                    json.loads(existing_summary_path.read_text(encoding="utf-8"))
                    if existing_summary_path.exists() else {}
                )
                return {
                    "ingestion_id": existing_id,
                    "sha256": checksum,
                    "normalization_context_sha256": context_sha256,
                    "duplicate": True,
                    "raw_object": str(next(manifest_path.parent.glob("source*"), manifest_path)),
                    "normalized_object": str(existing_normalized),
                    "summary": existing_summary,
                }

    ingestion_id = str(uuid.uuid4())
    raw_dir = raw_root / ingestion_id
    normalized_dir = normalized_root / ingestion_id
    raw_dir.mkdir(parents=True, exist_ok=False)
    normalized_dir.mkdir(parents=True, exist_ok=False)

    suffix = Path(original_filename or "upload.csv").suffix or ".csv"
    raw_path = raw_dir / f"source{suffix}"
    raw_path.write_bytes(content)
    manifest_path = raw_dir / "manifest.json"
    manifest_path.write_text(json.dumps({
        "ingestion_id": ingestion_id,
        "original_filename": original_filename,
        "sha256": checksum,
        "normalization_context_sha256": context_sha256,
        "normalization_context": normalization_context,
        "immutable_raw": True,
    }, indent=2), encoding="utf-8")
    # Application-level immutability for local development. Production object
    # storage will use retention/versioning policies instead of filesystem chmod.
    raw_path.chmod(0o444)
    manifest_path.chmod(0o444)

    records_path = normalized_dir / "records.jsonl"
    with records_path.open("w", encoding="utf-8") as handle:
        for record in normalized_result["records"]:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    summary = {k: v for k, v in normalized_result.items() if k != "records"}
    (normalized_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return {
        "ingestion_id": ingestion_id,
        "sha256": checksum,
        "normalization_context_sha256": context_sha256,
        "duplicate": False,
        "raw_object": str(raw_path),
        "normalized_object": str(records_path),
        "summary": summary,
    }
