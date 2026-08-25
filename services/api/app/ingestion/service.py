from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.ingestion.csv_ingest import inspect_csv, normalize_csv, persist_ingestion
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
        return inspect_csv(content)

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

    def ingest(
        self,
        content: bytes,
        filename: str,
        profile_name: str,
        *,
        source_identity: str | None = None,
    ) -> dict:
        profile = self.mapping_store.load(profile_name)
        result = normalize_csv(content, profile)
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
