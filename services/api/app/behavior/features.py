from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Iterable

from app.reconstruction.models import SignalPoint

CORE_PHASES = ("PRE_RINSE", "CAUSTIC", "INTERMEDIATE_RINSE", "ACID", "FINAL_RINSE", "SANITIZE")
METRICS = (
    "return_temperature_c",
    "return_flow_lpm",
    "return_conductivity_mscm",
    "return_pressure_bar",
)
METRIC_TO_CONCEPT = {
    "return_temperature_c": "cip.return.temperature",
    "return_flow_lpm": "cip.return.flow",
    "return_conductivity_mscm": "cip.return.conductivity",
    "return_pressure_bar": "cip.return.pressure",
}
METRIC_UNITS = {
    "return_temperature_c": "C",
    "return_flow_lpm": "L/min",
    "return_conductivity_mscm": "mS/cm",
    "return_pressure_bar": "bar",
}


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ValueError("behavioral intelligence requires timezone-aware cycle timestamps")
    return ts


def _usable(point: SignalPoint, metric: str) -> bool:
    value = getattr(point, metric)
    if value is None or not math.isfinite(value):
        return False
    return point.quality.get(METRIC_TO_CONCEPT[metric], "GOOD") in {"GOOD", "REDUNDANT"}


def _quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("quantile requires values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1 - weight) + ordered[hi] * weight


def _phase_points(points: Iterable[SignalPoint], asset: str, phase: dict) -> list[SignalPoint]:
    start = _parse_ts(phase["start_ts"])
    end = _parse_ts(phase["end_ts"])
    return sorted([p for p in points if p.asset == asset and start <= p.ts <= end], key=lambda p: p.ts)


def _profile(values_by_progress: list[tuple[float, float]], bins: int) -> list[float | None]:
    bucketed: list[list[float]] = [[] for _ in range(bins)]
    for progress, value in values_by_progress:
        idx = min(bins - 1, max(0, int(progress * bins)))
        bucketed[idx].append(value)
    return [statistics.median(bucket) if bucket else None for bucket in bucketed]


def extract_behavior_features(cycle: dict, points: list[SignalPoint], *, profile_bins: int = 8) -> dict:
    """Create robust scalar and time-normalized profile features for one reconstructed CIP cycle.

    Profiles are normalized to relative phase progress, so a 20-minute and a 22-minute
    phase can be compared by shape without pretending their absolute durations are equal.
    Duration remains a separate scalar feature.
    """
    asset = cycle["asset"]
    scalars: dict[str, dict] = {
        "cycle.duration_seconds": {
            "value": float(cycle["duration_seconds"]),
            "unit": "s",
            "family": "duration",
        }
    }
    profiles: dict[str, dict] = {}
    coverage: dict[str, float] = {}

    for phase in cycle.get("phases", []):
        phase_name = phase.get("phase")
        if phase_name not in CORE_PHASES:
            continue
        prefix = f"phase.{phase_name}"
        scalars[f"{prefix}.duration_seconds"] = {
            "value": float(phase["duration_seconds"]),
            "unit": "s",
            "family": "duration",
        }
        ppoints = _phase_points(points, asset, phase)
        if not ppoints:
            continue
        start = _parse_ts(phase["start_ts"])
        end = _parse_ts(phase["end_ts"])
        span = max((end - start).total_seconds(), 1.0)

        for metric in METRICS:
            usable = [p for p in ppoints if _usable(p, metric)]
            cov = len(usable) / len(ppoints)
            key_base = f"{prefix}.{metric}"
            coverage[key_base] = round(cov, 6)
            if not usable:
                continue
            vals = [float(getattr(p, metric)) for p in usable]
            scalars[f"{key_base}.median"] = {
                "value": statistics.median(vals),
                "unit": METRIC_UNITS[metric],
                "family": "level",
            }
            # Robust tails are more stable than raw extrema and still expose behavior changes.
            if len(vals) >= 5:
                scalars[f"{key_base}.p10"] = {
                    "value": _quantile(vals, 0.10),
                    "unit": METRIC_UNITS[metric],
                    "family": "tail",
                }
                scalars[f"{key_base}.p90"] = {
                    "value": _quantile(vals, 0.90),
                    "unit": METRIC_UNITS[metric],
                    "family": "tail",
                }

            progress_values = [
                (min(0.999999, max(0.0, (p.ts - start).total_seconds() / span)), float(getattr(p, metric)))
                for p in usable
            ]
            profiles[key_base] = {
                "values": _profile(progress_values, profile_bins),
                "unit": METRIC_UNITS[metric],
                "family": "profile",
            }

    return {
        "cycle_id": cycle["cycle_id"],
        "asset": asset,
        "start_ts": cycle["start_ts"],
        "scalars": scalars,
        "profiles": profiles,
        "coverage": coverage,
        "feature_contract": "robust-scalar-plus-time-normalized-phase-profile-v1",
    }
