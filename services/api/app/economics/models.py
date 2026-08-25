from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ResourcePolicy(BaseModel):
    minimum_meter_coverage: float = Field(default=0.95, ge=0.5, le=1.0)
    maximum_integration_gap_seconds: float = Field(default=30.0, gt=0)
    minimum_baseline_cycles: int = Field(default=20, ge=10)
    minimum_reference_cycles: int = Field(default=15, ge=8)
    excessive_threshold_fraction: float = Field(default=0.05, ge=0)


class CostProfile(BaseModel):
    """Plant-configured marginal economics. No industry-default rates are supplied."""

    name: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    approval_ref: str | None = None
    water_cost_per_m3: float | None = Field(default=None, ge=0)
    wastewater_cost_per_m3: float | None = Field(default=None, ge=0)
    electricity_cost_per_kwh: float | None = Field(default=None, ge=0)
    thermal_energy_cost_per_kwh: float | None = Field(default=None, ge=0)
    caustic_cost_per_kg: float | None = Field(default=None, ge=0)
    acid_cost_per_kg: float | None = Field(default=None, ge=0)
    sanitizer_cost_per_kg: float | None = Field(default=None, ge=0)
    incremental_production_value_per_hour: float | None = Field(default=None, ge=0)
    annual_cycles: float | None = Field(default=None, ge=0)
    notes: str | None = None

    @model_validator(mode="after")
    def normalize_currency(self) -> "CostProfile":
        self.currency = self.currency.upper()
        return self


class ResourceBaselineRequest(BaseModel):
    name: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    recipe_name: str = Field(min_length=1)
    recipe_revision: str = Field(min_length=1)
    ingestion_ids: list[str] = Field(min_length=1)
    policy: ResourcePolicy = Field(default_factory=ResourcePolicy)
    description: str | None = None
