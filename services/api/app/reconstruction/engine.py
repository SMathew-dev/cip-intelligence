from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .models import PhaseSegment, ReconstructedCycle, ReconstructionIssue, SignalPoint

CANONICAL_SEQUENCE = (
    "PRE_RINSE",
    "CAUSTIC",
    "INTERMEDIATE_RINSE",
    "ACID",
    "FINAL_RINSE",
    "SANITIZE",
)

PHASE_ALIASES = {
    "PRE_RINSE": "PRE_RINSE",
    "PRERINSE": "PRE_RINSE",
    "PRE RINSE": "PRE_RINSE",
    "INITIAL_RINSE": "PRE_RINSE",
    "INITIAL RINSE": "PRE_RINSE",
    "WATER_RINSE": "PRE_RINSE",
    "CAUSTIC": "CAUSTIC",
    "CAUSTIC_WASH": "CAUSTIC",
    "CAUSTIC WASH": "CAUSTIC",
    "ALKALI": "CAUSTIC",
    "ALKALINE": "CAUSTIC",
    "ALKALINE_WASH": "CAUSTIC",
    "RINSE_1": "INTERMEDIATE_RINSE",
    "RINSE 1": "INTERMEDIATE_RINSE",
    "INTERMEDIATE_RINSE": "INTERMEDIATE_RINSE",
    "INTERMEDIATE RINSE": "INTERMEDIATE_RINSE",
    "POST_CAUSTIC_RINSE": "INTERMEDIATE_RINSE",
    "POST CAUSTIC RINSE": "INTERMEDIATE_RINSE",
    "ACID": "ACID",
    "ACID_WASH": "ACID",
    "ACID WASH": "ACID",
    "FINAL_RINSE": "FINAL_RINSE",
    "FINAL RINSE": "FINAL_RINSE",
    "RINSE_2": "FINAL_RINSE",
    "RINSE 2": "FINAL_RINSE",
    "FINAL_WATER_RINSE": "FINAL_RINSE",
    "SANITIZE": "SANITIZE",
    "SANITIZER": "SANITIZE",
    "SANITIZATION": "SANITIZE",
}

INACTIVE_LABELS = {
    "IDLE", "OFF", "NOT_ACTIVE", "NOT ACTIVE", "COMPLETE", "COMPLETED", "END", "DRAIN", "DRAINING"
}


@dataclass(frozen=True)
class ReconstructionConfig:
    max_gap_seconds: float = 300.0
    inactive_flow_lpm: float = 50.0
    inactivity_split_seconds: float = 180.0
    transition_confirmation_points: int = 3
    explicit_glitch_max_points: int = 2
    caustic_conductivity_mscm: float = 30.0
    caustic_temperature_c: float = 50.0
    acid_min_conductivity_mscm: float = 8.0
    acid_max_conductivity_mscm: float = 30.0
    acid_temperature_c: float = 50.0


def canonicalize_phase(label: str | None) -> str | None:
    if label is None:
        return None
    clean = " ".join(str(label).strip().upper().replace("-", " ").replace("/", " ").split())
    underscore = clean.replace(" ", "_")
    if clean in INACTIVE_LABELS or underscore in INACTIVE_LABELS:
        return "INACTIVE"
    return PHASE_ALIASES.get(clean) or PHASE_ALIASES.get(underscore)


def _median_sample_seconds(points: list[SignalPoint]) -> float:
    if len(points) < 2:
        return 0.0
    gaps = [(b.ts - a.ts).total_seconds() for a, b in zip(points, points[1:])]
    positive = [g for g in gaps if g > 0 and math.isfinite(g)]
    return statistics.median(positive) if positive else 0.0


def _phase_metrics(points: list[SignalPoint]) -> dict:
    def stats(attr: str) -> dict | None:
        vals = [getattr(p, attr) for p in points if getattr(p, attr) is not None]
        if not vals:
            return None
        return {
            "min": min(vals),
            "mean": sum(vals) / len(vals),
            "max": max(vals),
            "samples": len(vals),
        }

    return {
        "return_temperature_c": stats("return_temperature_c"),
        "return_flow_lpm": stats("return_flow_lpm"),
        "return_conductivity_mscm": stats("return_conductivity_mscm"),
        "return_pressure_bar": stats("return_pressure_bar"),
    }


def _segment(points: list[SignalPoint], labels: list[str], sources: list[str], confidences: list[float]) -> list[PhaseSegment]:
    if not points:
        return []
    sample_s = _median_sample_seconds(points)
    segments: list[PhaseSegment] = []
    start = 0
    for idx in range(1, len(points) + 1):
        if idx == len(points) or labels[idx] != labels[start]:
            pts = points[start:idx]
            duration = max(0.0, (pts[-1].ts - pts[0].ts).total_seconds() + sample_s)
            segments.append(PhaseSegment(
                phase=labels[start],
                start_ts=pts[0].ts,
                end_ts=pts[-1].ts,
                duration_seconds=duration,
                sample_count=len(pts),
                evidence_source=sources[start],
                confidence=round(min(confidences[start:idx]), 3),
                metrics=_phase_metrics(pts),
                evidence={"sample_interval_seconds_estimate": sample_s},
            ))
            start = idx
    return segments


def _smooth_explicit_glitches(labels: list[str | None], max_points: int) -> tuple[list[str | None], int]:
    labels = list(labels)
    repaired = 0
    if len(labels) < 3 or max_points <= 0:
        return labels, repaired

    i = 0
    while i < len(labels):
        j = i + 1
        while j < len(labels) and labels[j] == labels[i]:
            j += 1
        run_len = j - i
        if run_len <= max_points and i > 0 and j < len(labels):
            if labels[i - 1] == labels[j] and labels[i - 1] is not None:
                replacement = labels[i - 1]
                for k in range(i, j):
                    labels[k] = replacement
                repaired += run_len
        i = j
    return labels, repaired


def _split_by_gaps_or_resets(points: list[SignalPoint], labels: list[str | None], cfg: ReconstructionConfig) -> list[tuple[list[SignalPoint], list[str | None]]]:
    if not points:
        return []
    blocks: list[tuple[list[SignalPoint], list[str | None]]] = []
    start = 0
    for i in range(1, len(points)):
        gap = (points[i].ts - points[i - 1].ts).total_seconds()
        reset = labels[i] == "PRE_RINSE" and labels[i - 1] in {"FINAL_RINSE", "SANITIZE", "INACTIVE"}
        if gap > cfg.max_gap_seconds or reset:
            if i > start:
                blocks.append((points[start:i], labels[start:i]))
            start = i
    if start < len(points):
        blocks.append((points[start:], labels[start:]))
    return blocks


def _split_active_blocks(points: list[SignalPoint], cfg: ReconstructionConfig) -> list[list[SignalPoint]]:
    """Split a signal stream into likely active CIP windows without inventing activity.

    Flow is used as the safest generic activity signal. Timestamp gaps also split.
    If flow is entirely unavailable, the caller may still reconstruct one contiguous
    block, but only if conductivity is present for phase inference.
    """
    if not points:
        return []
    if all(p.return_flow_lpm is None for p in points):
        blocks: list[list[SignalPoint]] = []
        start = 0
        for i in range(1, len(points)):
            if (points[i].ts - points[i - 1].ts).total_seconds() > cfg.max_gap_seconds:
                blocks.append(points[start:i])
                start = i
        blocks.append(points[start:])
        return [b for b in blocks if b]

    blocks: list[list[SignalPoint]] = []
    current: list[SignalPoint] = []
    inactive_start: datetime | None = None

    for point in points:
        active = point.return_flow_lpm is not None and point.return_flow_lpm >= cfg.inactive_flow_lpm
        if active:
            if current and (point.ts - current[-1].ts).total_seconds() > cfg.max_gap_seconds:
                blocks.append(current)
                current = []
            current.append(point)
            inactive_start = None
            continue

        if current:
            if inactive_start is None:
                inactive_start = point.ts
            if (point.ts - inactive_start).total_seconds() >= cfg.inactivity_split_seconds:
                blocks.append(current)
                current = []
                inactive_start = None

    if current:
        blocks.append(current)
    return blocks


def _infer_labels(points: list[SignalPoint], cfg: ReconstructionConfig) -> tuple[list[str] | None, list[float], list[ReconstructionIssue]]:
    issues: list[ReconstructionIssue] = []
    if not points:
        return None, [], issues
    cond_coverage = sum(p.return_conductivity_mscm is not None for p in points) / len(points)
    if cond_coverage < 0.8:
        issues.append(ReconstructionIssue(
            code="INSUFFICIENT_PHASE_EVIDENCE",
            severity="HIGH",
            message="Phase inference requires reliable return conductivity coverage; explicit CIP phase data was not available.",
            evidence={"conductivity_coverage": round(cond_coverage, 3), "required": 0.8},
        ))
        return None, [], issues

    # A forward-only state machine avoids classifying an early warm/dirty rinse as
    # acid simply because its conductivity happens to overlap an acid range.
    state = "PRE_RINSE"
    labels = [state] * len(points)
    confidence = [0.68] * len(points)
    candidate: str | None = None
    candidate_start: int | None = None
    candidate_count = 0

    def proposed(p: SignalPoint, current: str) -> tuple[str | None, float]:
        c = p.return_conductivity_mscm
        t = p.return_temperature_c
        if c is None:
            return None, 0.0
        if current == "PRE_RINSE":
            if c >= cfg.caustic_conductivity_mscm and t is not None and t >= cfg.caustic_temperature_c:
                return "CAUSTIC", 0.92
        elif current == "CAUSTIC":
            # First post-caustic rinse samples may still carry high conductivity;
            # the thermal collapse is therefore strong transition evidence.
            if t is not None and t < cfg.caustic_temperature_c:
                return "INTERMEDIATE_RINSE", 0.84
        elif current == "INTERMEDIATE_RINSE":
            if (
                t is not None and t >= cfg.acid_temperature_c
                and cfg.acid_min_conductivity_mscm <= c <= cfg.acid_max_conductivity_mscm
            ):
                return "ACID", 0.90
        elif current == "ACID":
            if t is not None and t < cfg.acid_temperature_c:
                return "FINAL_RINSE", 0.84
        return None, 0.0

    phase_confidence = {
        "PRE_RINSE": 0.68,
        "CAUSTIC": 0.92,
        "INTERMEDIATE_RINSE": 0.84,
        "ACID": 0.90,
        "FINAL_RINSE": 0.84,
    }

    for i, point in enumerate(points):
        pphase, pconf = proposed(point, state)
        if pphase is None:
            candidate = None
            candidate_start = None
            candidate_count = 0
            labels[i] = state
            confidence[i] = phase_confidence[state]
            continue

        if candidate == pphase:
            candidate_count += 1
        else:
            candidate = pphase
            candidate_start = i
            candidate_count = 1

        labels[i] = state
        confidence[i] = phase_confidence[state]
        if candidate_count >= cfg.transition_confirmation_points and candidate_start is not None:
            state = candidate
            for k in range(candidate_start, i + 1):
                labels[k] = state
                confidence[k] = pconf
            candidate = None
            candidate_start = None
            candidate_count = 0

    observed = set(labels)
    required = {"PRE_RINSE", "CAUSTIC", "INTERMEDIATE_RINSE", "ACID", "FINAL_RINSE"}
    missing = sorted(required - observed)
    if missing:
        issues.append(ReconstructionIssue(
            code="PARTIAL_PHASE_SEQUENCE",
            severity="MEDIUM",
            message="Signal-based reconstruction did not establish every expected core CIP phase.",
            evidence={"missing_phases": missing, "observed_phases": sorted(observed)},
        ))
    return labels, confidence, issues


def _cycle_id(asset: str, start: datetime, end: datetime) -> str:
    digest = hashlib.sha256(f"{asset}|{start.isoformat()}|{end.isoformat()}".encode()).hexdigest()[:16]
    return f"CIP-{digest}"


def _make_cycle(
    points: list[SignalPoint],
    labels: list[str],
    sources: list[str],
    confidences: list[float],
    issues: list[ReconstructionIssue],
) -> ReconstructedCycle:
    phases = tuple(_segment(points, labels, sources, confidences))
    core = [p.phase for p in phases if p.phase in CANONICAL_SEQUENCE]
    expected_core = ["PRE_RINSE", "CAUSTIC", "INTERMEDIATE_RINSE", "ACID", "FINAL_RINSE"]
    positions = {name: core.index(name) for name in set(core) if name in core}
    complete = all(name in positions for name in expected_core) and all(
        positions[a] < positions[b] for a, b in zip(expected_core, expected_core[1:])
    )
    sample_s = _median_sample_seconds(points)
    duration = max(0.0, (points[-1].ts - points[0].ts).total_seconds() + sample_s)
    mode_set = set(sources)
    mode = "HYBRID" if len(mode_set) > 1 else next(iter(mode_set))
    confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
    return ReconstructedCycle(
        cycle_id=_cycle_id(points[0].asset, points[0].ts, points[-1].ts),
        asset=points[0].asset,
        start_ts=points[0].ts,
        end_ts=points[-1].ts,
        duration_seconds=duration,
        phases=phases,
        reconstruction_mode=mode,
        confidence=confidence,
        completeness="COMPLETE" if complete else "PARTIAL",
        issues=tuple(issues),
    )


def _reconstruct_asset(points: list[SignalPoint], cfg: ReconstructionConfig) -> tuple[list[ReconstructedCycle], list[ReconstructionIssue]]:
    global_issues: list[ReconstructionIssue] = []
    points = sorted(points, key=lambda p: p.ts)
    # Duplicate timestamps for one asset are unsafe at this stage because SignalPoint
    # is already a pivoted view; duplicated rows would distort dwell calculations.
    dupes = sum(a.ts == b.ts for a, b in zip(points, points[1:]))
    if dupes:
        global_issues.append(ReconstructionIssue(
            code="DUPLICATE_TIMESTAMPS",
            severity="HIGH",
            message="Duplicate timestamp-level signal points were found after normalization; reconstruction withheld.",
            evidence={"duplicate_pairs": dupes, "asset": points[0].asset if points else None},
        ))
        return [], global_issues

    canonical = [canonicalize_phase(p.explicit_phase) for p in points]
    explicit_known = sum(label not in {None, "INACTIVE"} for label in canonical)
    explicit_coverage = explicit_known / len(points) if points else 0.0

    cycles: list[ReconstructedCycle] = []
    if explicit_coverage >= 0.8:
        smoothed, repaired = _smooth_explicit_glitches(canonical, cfg.explicit_glitch_max_points)
        if repaired:
            global_issues.append(ReconstructionIssue(
                code="EXPLICIT_PHASE_GLITCH_REPAIRED",
                severity="INFO",
                message="Short A-B-A phase-label glitches were repaired before segmentation.",
                evidence={"repaired_points": repaired, "asset": points[0].asset},
            ))
        blocks = _split_by_gaps_or_resets(points, smoothed, cfg)
        for block_points, block_labels in blocks:
            active_pairs = [(p, lab) for p, lab in zip(block_points, block_labels) if lab not in {None, "INACTIVE"}]
            if not active_pairs:
                continue
            bp = [x[0] for x in active_pairs]
            bl = [x[1] for x in active_pairs]
            unknown_labels = [p.explicit_phase for p, lab in zip(block_points, block_labels) if p.explicit_phase and lab is None]
            local_issues: list[ReconstructionIssue] = []
            if unknown_labels:
                local_issues.append(ReconstructionIssue(
                    code="UNKNOWN_EXPLICIT_PHASE_LABEL",
                    severity="MEDIUM",
                    message="One or more explicit phase labels were not recognized and were excluded from the reconstructed sequence.",
                    evidence={"labels": sorted(set(unknown_labels))},
                ))
            sources = ["EXPLICIT"] * len(bp)
            conf = [0.995 if repaired == 0 else 0.98] * len(bp)
            cycles.append(_make_cycle(bp, bl, sources, conf, local_issues))
        return cycles, global_issues

    # No sufficiently complete explicit step signal: infer only from supported
    # process evidence. Unknown is allowed and preferable to a fabricated cycle.
    blocks = _split_active_blocks(points, cfg)
    for block in blocks:
        labels, conf, local_issues = _infer_labels(block, cfg)
        if labels is None:
            global_issues.extend(local_issues)
            continue
        cycles.append(_make_cycle(block, labels, ["INFERRED"] * len(block), conf, local_issues))
    if not cycles and not global_issues:
        global_issues.append(ReconstructionIssue(
            code="NO_CIP_WINDOW_ESTABLISHED",
            severity="HIGH",
            message="No sufficiently supported CIP activity window could be established from the available signals.",
            evidence={"asset": points[0].asset if points else None},
        ))
    return cycles, global_issues


def reconstruct_cycles(points: Iterable[SignalPoint], config: ReconstructionConfig | None = None) -> dict:
    cfg = config or ReconstructionConfig()
    by_asset: dict[str, list[SignalPoint]] = {}
    for point in points:
        by_asset.setdefault(point.asset, []).append(point)

    all_cycles: list[ReconstructedCycle] = []
    issues: list[ReconstructionIssue] = []
    for asset, asset_points in sorted(by_asset.items()):
        cycles, asset_issues = _reconstruct_asset(asset_points, cfg)
        all_cycles.extend(cycles)
        issues.extend(asset_issues)

    all_cycles.sort(key=lambda c: (c.asset, c.start_ts))
    return {
        "cycles": [c.to_dict() for c in all_cycles],
        "cycle_count": len(all_cycles),
        "issues": [
            {"code": i.code, "severity": i.severity, "message": i.message, "evidence": i.evidence}
            for i in issues
        ],
        "principle": "Explicit evidence is preferred; signal inference is labeled and may return unknown.",
    }
