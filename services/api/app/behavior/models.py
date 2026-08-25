from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class BehaviorPolicy(BaseModel):
    """Conservative defaults for an asset/recipe-specific behavioral baseline."""

    minimum_baseline_cycles: int = Field(default=20, ge=10)
    minimum_feature_cycles: int = Field(default=15, ge=8)
    require_explicit_reconstruction: bool = True
    minimum_reconstruction_confidence: float = Field(default=0.95, ge=0, le=1)
    profile_bins: int = Field(default=8, ge=4, le=24)
    warning_robust_z: float = Field(default=3.5, gt=0)
    high_robust_z: float = Field(default=5.0, gt=0)
    training_screen_robust_z: float = Field(default=6.0, gt=0)
    training_screen_extreme_z: float = Field(default=10.0, gt=0)
    training_screen_feature_count: int = Field(default=2, ge=1)
    minimum_profile_adjacent_bins: int = Field(default=2, ge=2)
    maximum_reported_deviations: int = Field(default=12, ge=1, le=50)

    @model_validator(mode="after")
    def validate_thresholds(self) -> "BehaviorPolicy":
        if self.high_robust_z <= self.warning_robust_z:
            raise ValueError("high_robust_z must exceed warning_robust_z")
        if self.training_screen_robust_z <= self.high_robust_z:
            raise ValueError("training_screen_robust_z must exceed high_robust_z")
        if self.training_screen_extreme_z <= self.training_screen_robust_z:
            raise ValueError("training_screen_extreme_z must exceed training_screen_robust_z")
        if self.minimum_feature_cycles > self.minimum_baseline_cycles:
            raise ValueError("minimum_feature_cycles cannot exceed minimum_baseline_cycles")
        return self


class BehaviorBaselineRequest(BaseModel):
    name: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    recipe_name: str = Field(min_length=1)
    recipe_revision: str = Field(min_length=1)
    ingestion_ids: list[str] = Field(min_length=1)
    description: str | None = None
    policy: BehaviorPolicy = Field(default_factory=BehaviorPolicy)


BehaviorAssessment = Literal[
    "NORMAL",
    "UNUSUAL",
    "HIGHLY_UNUSUAL",
    "NOT_EVALUABLE",
]
