from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class QualityIssue:
    code: str
    severity: str
    message: str
    evidence: dict


def detect_flatline(
    values: Iterable[float],
    *,
    min_consecutive_points: int = 12,
    tolerance: float = 1e-9,
) -> list[QualityIssue]:
    """Detect a suspicious consecutive constant-value run.

    The default assumes roughly 10-second data and catches >=2 minutes of exact flatline.
    Production logic will make duration sampling-rate aware and compare related signals.
    """
    vals = list(values)
    if len(vals) < min_consecutive_points:
        return []

    best_start = 0
    best_len = 1
    run_start = 0
    run_len = 1

    for idx in range(1, len(vals)):
        if abs(vals[idx] - vals[idx - 1]) <= tolerance:
            run_len += 1
        else:
            if run_len > best_len:
                best_start, best_len = run_start, run_len
            run_start = idx
            run_len = 1

    if run_len > best_len:
        best_start, best_len = run_start, run_len

    if best_len >= min_consecutive_points:
        frozen_value = vals[best_start]
        return [QualityIssue(
            code="FLATLINE",
            severity="HIGH",
            message=(
                f"Signal is flat at {frozen_value} for {best_len} consecutive samples; "
                "dependent conclusions should be downgraded until corroborated."
            ),
            evidence={
                "start_index": best_start,
                "consecutive_samples": best_len,
                "value": frozen_value,
            },
        )]
    return []


def detect_range(values: Iterable[float], low: float, high: float, concept: str) -> list[QualityIssue]:
    vals = list(values)
    bad = [v for v in vals if v < low or v > high]
    if not bad:
        return []
    return [QualityIssue(
        code="IMPOSSIBLE_OR_SUSPICIOUS_RANGE",
        severity="HIGH",
        message=f"{concept}: {len(bad)} sample(s) outside configured plausible range [{low}, {high}].",
        evidence={"bad_count": len(bad), "low": low, "high": high},
    )]
