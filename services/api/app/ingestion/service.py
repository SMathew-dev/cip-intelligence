from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.ingestion.csv_ingest import inspect_csv, normalize_csv, parse_csv_bytes, persist_ingestion
from app.ingestion.discovery import discover_timestamp_candidates, infer_generic_measurement, profile_values
from app.ingestion.models import MappingProfile
from app.ingestion.mapping_store import MappingStore
from app.ingestion.semantic_registry import get_concept
from app.ingestion.units import normalize_unit


class IngestionService:
    def __init__(self, runtime_root: Path):
        self.runtime_root = runtime_root
        self.raw_root = runtime_root / "raw"
        self.normalized_root = runtime_root / "normalized"
        self.mapping_store = MappingStore(runtime_root / "mappings")
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self.normalized_root.mkdir(parents=True, exist_ok=True)

    def inspect(self, content: bytes) -> dict:
        """Inspect structure plus conservative value evidence before mapping.

        Header semantics remain the primary source of approved mapping candidates.
        Value profiling improves discovery of timestamps and generic measurement
        families, but never invents equipment identity or CIP direction.
        """
        result = inspect_csv(content)
        table = parse_csv_bytes(content, max_rows=250)

        timestamp_candidates = discover_timestamp_candidates(table.headers, table.rows)
        header_timestamp = result.get("timestamp_candidate") or {}
        header_column = header_timestamp.get("column")
        if header_column:
            existing = next((c for c in timestamp_candidates if c["column"] == header_column), None)
            header_confidence = float(header_timestamp.get("confidence") or 0.0)
            if existing:
                existing["confidence"] = round(max(existing["confidence"], header_confidence), 3)
                if header_confidence > 0 and "header alias" not in existing["reason"]:
                    existing["reason"] = (existing["reason"] + "; header alias").strip("; ")
            elif header_confidence >= 0.55:
                timestamp_candidates.append({
                    "column": header_column,
                    "confidence": round(header_confidence, 3),
                    "parse_fraction": 0.0,
                    "monotonic_fraction": 0.0,
                    "unique_fraction": 0.0,
                    "reason": "timestamp header alias; values still require review",
                })
            timestamp_candidates.sort(key=lambda c: (-c["confidence"], c["column"]))

        if timestamp_candidates:
            best = timestamp_candidates[0]
            result["timestamp_candidate"] = {
                "column": best["column"],
                "confidence": best["confidence"],
                "reason": best["reason"],
                "value_supported": best.get("parse_fraction", 0) >= 0.8,
            }
        result["timestamp_candidates"] = timestamp_candidates

        by_name = {column["source_column"]: column for column in result["columns"]}
        for header in table.headers:
            column = by_name[header]
            values = [row.get(header, "") for row in table.rows]
            value_profile = profile_values(values)
            column["value_profile"] = value_profile
            column["numeric_fraction"] = value_profile["numeric_fraction"]
            column["timestamp_confidence"] = next(
                (c["confidence"] for c in timestamp_candidates if c["column"] == header), 0.0
            )
            if not column.get("mapping_candidates") and header != result["timestamp_candidate"].get("column"):
                column["measurement_candidate"] = infer_generic_measurement(
                    header,
                    numeric_fraction=value_profile["numeric_fraction"],
                )
            else:
                column["measurement_candidate"] = None

        result["inspection_version"] = "1.2-semantic-discovery"
        result["discovery_principle"] = (
            "Value evidence can propose timestamps and measurement families; equipment identity, CIP direction, "
            "engineering units, and plant semantics still require explicit confirmation before analysis."
        )
        return result

    def validate_mapping(self, profile: MappingProfile) -> list[str]:
        errors: list[str] = []
        try:
            ZoneInfo(profile.timezone)
        except ZoneInfoNotFoundError:
            errors.append(f"Unknown IANA timezone: {profile.timezone!r}")

        for mapping in profile.mappings:
            semantic = get_concept(mapping.concept)
            if semantic is None:
                errors.append(f"Unknown semantic concept: {mapping.concept!r}")
                continue
            if mapping.asset_override is not None and not mapping.asset_override.strip():
                errors.append(f"{mapping.source_column!r} has a blank asset_override")
            if semantic.canonical_unit is not None and not mapping.source_unit:
                errors.append(
                    f"{mapping.source_column!r} -> {mapping.concept} requires an explicit source_unit; "
                    "CIP Intelligence will not silently assume engineering units."
                )
            if mapping.source_unit and semantic.canonical_unit:
                # Basic unit-family check via a harmless conversion.
                try:
                    from app.ingestion.units import convert_value
                    convert_value(1.0, normalize_unit(mapping.source_unit), semantic.canonical_unit)
                except ValueError as exc:
                    errors.append(str(exc))
        return errors

    def save_mapping(self, profile: MappingProfile) -> dict:
        errors = self.validate_mapping(profile)
        if errors:
            raise ValueError("; ".join(errors))
        path = self.mapping_store.save(profile)
        return {"saved": True, "name": profile.name, "path": str(path)}

    def _normalize_with_asset_overrides(self, content: bytes, profile: MappingProfile) -> dict:
        """Normalize wide historian exports without collapsing circuits together.

        A reviewed ``asset_override`` means the equipment identity came from a tag
        prefix such as POL1/POL2 rather than from a row-level Circuit column. Each
        asset is normalized independently and then merged into one lineage result.
        """
        overridden = [m for m in profile.mappings if m.asset_override]
        if not overridden:
            return normalize_csv(content, profile)

        asset_mapping = next((m for m in profile.mappings if m.concept == "cip.asset"), None)
        if asset_mapping is not None:
            raise ValueError("Use either a row-level asset mapping or per-signal asset overrides, not both.")

        unassigned = [m.source_column for m in profile.mappings if not m.asset_override]
        if unassigned:
            raise ValueError(
                "Wide-format equipment mapping requires an approved asset assignment for every mapped signal; "
                f"missing assignments: {unassigned}"
            )

        grouped: dict[str, list] = {}
        for mapping in profile.mappings:
            asset = str(mapping.asset_override).strip()
            grouped.setdefault(asset, []).append(mapping.model_copy(update={"asset_override": None}))

        results: list[dict] = []
        for asset, mappings in sorted(grouped.items()):
            scoped = profile.model_copy(update={"asset_default": asset, "mappings": mappings})
            result = normalize_csv(content, scoped)
            for issue in result.get("issues", []):
                issue.setdefault("asset", asset)
            results.append(result)

        rows = results[0]["rows_in_source"] if results else 0
        records = [record for result in results for record in result["records"]]
        issues = [issue for result in results for issue in result["issues"]]
        good = sum(result["good_points"] for result in results)
        total = sum(result["normalized_points"] for result in results)
        return {
            "rows_in_source": rows,
            "normalized_points": total,
            "good_points": good,
            "data_coverage": round(good / total, 4) if total else 0.0,
            "high_severity_issue_count": sum(1 for issue in issues if issue.get("severity") == "HIGH"),
            "issues": issues,
            "records": records,
        }

    def ingest(
        self,
        content: bytes,
        filename: str,
        profile_name: str,
        *,
        source_identity: str | None = None,
    ) -> dict:
        profile = self.mapping_store.load(profile_name)
        result = self._normalize_with_asset_overrides(content, profile)
        persisted = persist_ingestion(
            content,
            original_filename=filename,
            normalized_result=result,
            raw_root=self.raw_root,
            normalized_root=self.normalized_root,
            normalization_context={
                "mapping_profile": profile.model_dump(mode="json"),
                "source_identity": source_identity or "manual-upload",
            },
        )
        # Do not return every normalized point through the API by default.
        return persisted
