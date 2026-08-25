from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class OptimizationPolicy(BaseModel):
    """Plant-approved governance policy for optimization discovery.

    These are product guardrails, not dairy-industry validation standards. A real
    facility must approve its own values and QA requirements.
    """

    minimum_reference_cycles: int = Field(default=30, ge=15)
    minimum_outcome_cycles: int = Field(default=20, ge=5)
    minimum_outcome_coverage: float = Field(default=0.80, ge=0, le=1)
    minimum_historical_pass_rate: float = Field(default=0.98, ge=0, le=1)
    minimum_endpoint_margin_seconds: float = Field(default=60.0, ge=0)
    minimum_time_saving_seconds: float = Field(default=60.0, ge=0)
    trial_guard_band_seconds: float = Field(default=60.0, ge=0)
    maximum_single_trial_reduction_fraction: float = Field(default=0.40, gt=0, le=0.75)
    block_on_high_diagnostic_hypothesis: bool = True
    block_on_confirmed_unresolved_condition: bool = True
    require_qa_for_hygiene_sensitive_change: bool = True
    minimum_trial_cycles: int = Field(default=10, ge=3)
    maximum_trial_failed_verifications: int = Field(default=0, ge=0)


class OutcomeHistorySummary(BaseModel):
    comparable_cycles: int = Field(ge=0)
    cycles_with_verification: int = Field(ge=0)
    passed_verifications: int = Field(ge=0)
    failed_verifications: int = Field(ge=0)
    borderline_or_inconclusive: int = Field(default=0, ge=0)
    evidence_scope: str = "asset+recipe comparable historical cycles"

    @model_validator(mode="after")
    def consistent_counts(self) -> "OutcomeHistorySummary":
        if self.cycles_with_verification > self.comparable_cycles:
            raise ValueError("cycles_with_verification cannot exceed comparable_cycles")
        if self.passed_verifications + self.failed_verifications + self.borderline_or_inconclusive > self.cycles_with_verification:
            raise ValueError("verification outcome counts exceed cycles_with_verification")
        return self


class TrialCycleResult(BaseModel):
    cycle_id: str
    l2_assessment: Literal["COMPLIANT", "PROCESS_DEVIATION", "DATA_REVIEW_REQUIRED", "NOT_EVALUABLE"]
    verification_outcome: Literal["PASS", "FAIL", "BORDERLINE", "INCONCLUSIVE", "NOT_AVAILABLE"] = "NOT_AVAILABLE"
    diagnostic_status: str = "NO_FINDINGS"
    measured_savings: dict = Field(default_factory=dict)


class ControlledTrialAssessmentRequest(BaseModel):
    candidate_id: str
    results: list[TrialCycleResult]
    engineering_approval_ref: str | None = None
    qa_approval_ref: str | None = None
    protocol_ref: str | None = None


class OptimizationDecisionRecord(BaseModel):
    """Human governance record. The engine never creates approvals itself."""

    candidate_id: str
    decision: Literal["APPROVED_FOR_TRIAL", "REJECTED", "DEFERRED", "ACCEPTED_AFTER_VALIDATION", "REJECTED_AFTER_VALIDATION"]
    decided_at: datetime
    decided_by_role: str
    decision_ref: str
    notes: str | None = None

    @model_validator(mode="after")
    def aware(self) -> "OptimizationDecisionRecord":
        if self.decided_at.tzinfo is None:
            raise ValueError("optimization decisions require timezone-aware timestamps")
        return self
