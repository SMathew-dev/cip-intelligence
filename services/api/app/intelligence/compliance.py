from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    code: str
    finding_class: str
    severity: str
    title: str
    conclusion: str
    confidence: float | None
    evidence: dict


def minimum_requirement(
    *,
    code: str,
    title: str,
    actual: float,
    minimum: float,
    unit: str,
    evidence_quality: float = 1.0,
) -> Finding:
    passed = actual >= minimum
    return Finding(
        code=code,
        finding_class="DERIVED",
        severity="OK" if passed else "CRITICAL",
        title=title,
        conclusion=(
            f"Requirement achieved: {actual:.2f} {unit} >= {minimum:.2f} {unit}."
            if passed
            else f"Requirement not achieved: {actual:.2f} {unit} < {minimum:.2f} {unit}."
        ),
        confidence=evidence_quality,
        evidence={"actual": actual, "minimum": minimum, "unit": unit},
    )
