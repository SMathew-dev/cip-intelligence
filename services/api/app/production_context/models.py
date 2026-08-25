from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SourceType = Literal["MES", "HISTORIAN", "DATABASE", "API", "CSV", "MANUAL", "SIMULATOR"]
ContextAssessment = Literal[
    "CONTEXTUALLY_TYPICAL",
    "CONTEXTUALLY_UNUSUAL",
    "INSUFFICIENT_COMPARABLES",
    "NOT_EVALUABLE",
]


class ProductionRunMetrics(BaseModel):
    """Run-level production evidence used by L4.

    These fields are deliberately engineering observations, not a generic 'soil score'.
    Plants may provide any subset. Missing fields remain missing rather than being guessed.
    """

    average_throughput_lph: float | None = Field(default=None, ge=0)
    total_volume_l: float | None = Field(default=None, ge=0)
    fat_pct: float | None = Field(default=None, ge=0, le=100)
    protein_pct: float | None = Field(default=None, ge=0, le=100)
    total_solids_pct: float | None = Field(default=None, ge=0, le=100)
    process_temperature_avg_c: float | None = Field(default=None, ge=-20, le=250)
    process_temperature_max_c: float | None = Field(default=None, ge=-20, le=250)
    shutdown_minutes: float | None = Field(default=None, ge=0)
    pressure_drop_start_bar: float | None = Field(default=None, ge=-1, le=100)
    pressure_drop_end_bar: float | None = Field(default=None, ge=-1, le=100)
    normalized_heat_transfer_start: float | None = Field(default=None, gt=0)
    normalized_heat_transfer_end: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_consistency(self) -> "ProductionRunMetrics":
        if self.process_temperature_avg_c is not None and self.process_temperature_max_c is not None:
            if self.process_temperature_max_c < self.process_temperature_avg_c:
                raise ValueError("process_temperature_max_c cannot be below process_temperature_avg_c")
        return self


class ProductionRun(BaseModel):
    run_id: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    product_code: str = Field(min_length=1)
    product_family: str | None = None
    batch_ref: str | None = None
    start_ts: datetime
    end_ts: datetime
    source_type: SourceType
    source_ref: str = Field(min_length=1)
    metrics: ProductionRunMetrics = Field(default_factory=ProductionRunMetrics)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_run(self) -> "ProductionRun":
        if self.start_ts.tzinfo is None or self.end_ts.tzinfo is None:
            raise ValueError("production run timestamps must be timezone-aware")
        if self.end_ts <= self.start_ts:
            raise ValueError("production run end_ts must be after start_ts")
        return self


class ContextPolicy(BaseModel):
    max_lookback_hours: float = Field(default=48.0, gt=0, le=720)
    max_inter_run_gap_hours: float = Field(default=4.0, ge=0, le=72)
    long_pre_cip_idle_hours: float = Field(default=2.0, ge=0, le=72)
    minimum_training_cycles: int = Field(default=20, ge=10)
    minimum_comparable_cycles: int = Field(default=8, ge=5)
    maximum_neighbors: int = Field(default=20, ge=5, le=100)
    minimum_shared_context_features: int = Field(default=3, ge=2, le=20)
    max_context_distance: float = Field(default=2.75, gt=0)
    require_same_product_family: bool = True
    warning_robust_z: float = Field(default=3.5, gt=0)
    high_robust_z: float = Field(default=5.0, gt=0)
    maximum_reported_differences: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def validate_policy(self) -> "ContextPolicy":
        if self.high_robust_z <= self.warning_robust_z:
            raise ValueError("high_robust_z must exceed warning_robust_z")
        if self.minimum_comparable_cycles > self.minimum_training_cycles:
            raise ValueError("minimum_comparable_cycles cannot exceed minimum_training_cycles")
        return self


class ContextBaselineRequest(BaseModel):
    name: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    recipe_name: str = Field(min_length=1)
    recipe_revision: str = Field(min_length=1)
    ingestion_ids: list[str] = Field(min_length=1)
    description: str | None = None
    policy: ContextPolicy = Field(default_factory=ContextPolicy)
