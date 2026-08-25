from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MetricName = Literal[
    "return_temperature_c",
    "return_flow_lpm",
    "return_conductivity_mscm",
    "return_pressure_bar",
]
Operator = Literal["gte", "lte", "between"]
FlatlinePolicy = Literal["BLOCK", "ALLOW_STABLE_ENDPOINT"]
RequirementKind = Literal[
    "PHASE_DURATION",
    "CONTINUOUS_LIMIT",
    "QUALIFIED_EXPOSURE",
    "CONCURRENT_EXPOSURE",
    "ENDPOINT",
]


class Condition(BaseModel):
    metric: MetricName
    operator: Operator
    minimum: float | None = None
    maximum: float | None = None
    unit: str

    @model_validator(mode="after")
    def validate_bounds(self) -> "Condition":
        if self.operator == "gte" and self.minimum is None:
            raise ValueError("gte condition requires minimum")
        if self.operator == "lte" and self.maximum is None:
            raise ValueError("lte condition requires maximum")
        if self.operator == "between":
            if self.minimum is None or self.maximum is None:
                raise ValueError("between condition requires minimum and maximum")
            if self.minimum > self.maximum:
                raise ValueError("condition minimum cannot exceed maximum")
        return self


class ComplianceRequirement(BaseModel):
    code: str = Field(min_length=1)
    title: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    kind: RequirementKind
    conditions: list[Condition] = Field(default_factory=list)
    minimum_seconds: float | None = Field(default=None, ge=0)
    allowed_excursion_seconds: float = Field(default=0.0, ge=0)
    minimum_data_coverage: float = Field(default=0.95, ge=0, le=1)
    endpoint_hold_seconds: float | None = Field(default=None, ge=0)
    require_explicit_phase: bool | None = None
    flatline_policy: FlatlinePolicy = "BLOCK"

    @model_validator(mode="after")
    def validate_shape(self) -> "ComplianceRequirement":
        if self.kind == "PHASE_DURATION":
            if self.minimum_seconds is None:
                raise ValueError("PHASE_DURATION requires minimum_seconds")
            if self.conditions:
                raise ValueError("PHASE_DURATION does not accept conditions")
        elif self.kind in {"CONTINUOUS_LIMIT", "QUALIFIED_EXPOSURE", "ENDPOINT"}:
            if len(self.conditions) != 1:
                raise ValueError(f"{self.kind} requires exactly one condition")
        elif self.kind == "CONCURRENT_EXPOSURE":
            if len(self.conditions) < 2:
                raise ValueError("CONCURRENT_EXPOSURE requires at least two simultaneous conditions")

        if self.kind in {"QUALIFIED_EXPOSURE", "CONCURRENT_EXPOSURE"} and self.minimum_seconds is None:
            raise ValueError(f"{self.kind} requires minimum_seconds")
        if self.kind == "ENDPOINT" and self.endpoint_hold_seconds is None:
            raise ValueError("ENDPOINT requires endpoint_hold_seconds")
        if self.flatline_policy == "ALLOW_STABLE_ENDPOINT" and self.kind != "ENDPOINT":
            raise ValueError("ALLOW_STABLE_ENDPOINT is only valid for ENDPOINT requirements")
        return self


class RecipePolicy(BaseModel):
    allow_inferred_phase_for_compliance: bool = False
    minimum_phase_confidence: float = Field(default=0.95, ge=0, le=1)
    flatline_duration_seconds: float = Field(default=120.0, gt=0)
    max_sample_interval_factor: float = Field(default=2.5, ge=1.0)


class ValidatedRecipe(BaseModel):
    name: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    effective_from: datetime
    effective_to: datetime | None = None
    approval_ref: str = Field(min_length=1)
    requirements: list[ComplianceRequirement] = Field(min_length=1)
    policy: RecipePolicy = Field(default_factory=RecipePolicy)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_recipe(self) -> "ValidatedRecipe":
        if self.effective_from.tzinfo is None:
            raise ValueError("effective_from must be timezone-aware")
        if self.effective_to is not None:
            if self.effective_to.tzinfo is None:
                raise ValueError("effective_to must be timezone-aware")
            if self.effective_to <= self.effective_from:
                raise ValueError("effective_to must be after effective_from")
        codes = [r.code for r in self.requirements]
        if len(codes) != len(set(codes)):
            raise ValueError("requirement codes must be unique within a recipe revision")
        return self
