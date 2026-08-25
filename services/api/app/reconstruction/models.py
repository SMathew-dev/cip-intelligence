from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SignalPoint:
    ts: datetime
    asset: str
    return_temperature_c: float | None = None
    return_flow_lpm: float | None = None
    return_conductivity_mscm: float | None = None
    return_pressure_bar: float | None = None
    explicit_phase: str | None = None
    quality: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReconstructionIssue:
    code: str
    severity: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PhaseSegment:
    phase: str
    start_ts: datetime
    end_ts: datetime
    duration_seconds: float
    sample_count: int
    evidence_source: str  # EXPLICIT or INFERRED
    confidence: float
    metrics: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["start_ts"] = self.start_ts.isoformat()
        out["end_ts"] = self.end_ts.isoformat()
        return out


@dataclass(frozen=True)
class ReconstructedCycle:
    cycle_id: str
    asset: str
    start_ts: datetime
    end_ts: datetime
    duration_seconds: float
    phases: tuple[PhaseSegment, ...]
    reconstruction_mode: str  # EXPLICIT / INFERRED / HYBRID
    confidence: float
    completeness: str  # COMPLETE / PARTIAL
    issues: tuple[ReconstructionIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "asset": self.asset,
            "start_ts": self.start_ts.isoformat(),
            "end_ts": self.end_ts.isoformat(),
            "duration_seconds": self.duration_seconds,
            "reconstruction_mode": self.reconstruction_mode,
            "confidence": self.confidence,
            "completeness": self.completeness,
            "phases": [p.to_dict() for p in self.phases],
            "issues": [asdict(i) for i in self.issues],
        }
