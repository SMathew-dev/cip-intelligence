from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class MappingField(BaseModel):
    source_column: str
    concept: str
    source_unit: str | None = None
    scale_factor: float = Field(default=1.0, allow_inf_nan=False)
    offset_value: float = Field(default=0.0, allow_inf_nan=False)
    # Wide historian exports often encode equipment identity in the tag name
    # (for example POL1 Return Flow, POL2 Return Flow). The mapping therefore
    # needs a reviewed per-signal asset assignment instead of collapsing every
    # signal into one profile-level default asset.
    asset_override: str | None = None


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
