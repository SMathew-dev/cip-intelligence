from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class MappingField(BaseModel):
    source_column: str
    concept: str
    source_unit: str | None = None
    scale_factor: float = 1.0
    offset_value: float = 0.0


class MappingProfile(BaseModel):
    name: str
    plant: str
    source_system: str
    timezone: str = "UTC"
    timestamp_column: str
    asset_default: str | None = None
    mappings: list[MappingField] = Field(default_factory=list)

    @model_validator(mode="after")
    def no_duplicate_source_columns(self) -> "MappingProfile":
        columns = [m.source_column for m in self.mappings]
        duplicates = sorted({c for c in columns if columns.count(c) > 1})
        if duplicates:
            raise ValueError(f"duplicate mapped source columns: {duplicates}")
        return self
