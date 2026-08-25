from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, model_validator

EvidenceClass = Literal["MEASURED", "DERIVED", "INFERRED", "CONFIRMED", "UNKNOWN"]
QAType = Literal["ATP", "MICRO", "ALLERGEN", "VISUAL", "TITRATION", "OTHER"]
Outcome = Literal["PASS", "FAIL", "BORDERLINE", "INCONCLUSIVE"]
SourceType = Literal["LIMS", "QA_SYSTEM", "CMMS", "HISTORIAN", "API", "CSV", "MANUAL", "SIMULATOR"]


class QAResult(BaseModel):
    result_id: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    sample_ts: datetime
    result_type: QAType
    outcome: Outcome
    value: float | None = None
    unit: str | None = None
    location: str | None = None
    method: str | None = None
    source_type: SourceType
    source_ref: str = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ts(self) -> "QAResult":
        if self.sample_ts.tzinfo is None:
            raise ValueError("QA sample timestamp must be timezone-aware")
        return self


class MaintenanceEvent(BaseModel):
    event_id: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    event_ts: datetime
    component: str | None = None
    action: str = Field(min_length=1)
    finding_code: str | None = None
    finding_text: str | None = None
    confirmation_status: Literal["CONFIRMED", "NOT_CONFIRMED", "UNKNOWN"] = "UNKNOWN"
    source_type: SourceType
    source_ref: str = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ts(self) -> "MaintenanceEvent":
        if self.event_ts.tzinfo is None:
            raise ValueError("maintenance event timestamp must be timezone-aware")
        return self


class OperatorObservation(BaseModel):
    observation_id: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    event_ts: datetime
    category: Literal["VISIBLE_SOIL", "SPRAY_DEVICE", "VALVE", "PUMP", "FOAM", "SENSOR", "MANUAL_EXTENSION", "OTHER"]
    text: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ts(self) -> "OperatorObservation":
        if self.event_ts.tzinfo is None:
            raise ValueError("operator observation timestamp must be timezone-aware")
        return self


class DiagnosisPolicy(BaseModel):
    qa_link_hours_after_cip: float = Field(default=12.0, gt=0, le=168)
    maintenance_link_hours_after_cip: float = Field(default=168.0, gt=0, le=2160)
    operator_link_hours_before_cip: float = Field(default=2.0, ge=0, le=72)
    operator_link_hours_after_cip: float = Field(default=24.0, ge=0, le=168)
    minimum_historical_confirmations: int = Field(default=5, ge=1)
    minimum_empirical_precision: float = Field(default=0.60, ge=0, le=1)
    max_hypotheses: int = Field(default=6, ge=1, le=20)


class DiagnosticCase(BaseModel):
    case_id: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    diagnosis_code: str = Field(min_length=1)
    predicted_ts: datetime
    confirmed_code: str | None = None
    confirmation_status: Literal["CONFIRMED", "NOT_CONFIRMED", "UNRESOLVED"] = "UNRESOLVED"
    confirmation_ref: str | None = None
    signature: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ts(self) -> "DiagnosticCase":
        if self.predicted_ts.tzinfo is None:
            raise ValueError("diagnostic case timestamp must be timezone-aware")
        return self
