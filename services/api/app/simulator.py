from __future__ import annotations

import csv
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


def generate_cycle_rows(
    scenario: str = "normal",
    seed: int = 7,
    *,
    start: datetime | None = None,
    asset: str = "HTST-01",
) -> list[dict]:
    """Generate deterministic simulator rows in memory.

    ``generate_cycle`` remains the public CSV fixture helper; M11 uses this
    in-memory form so facility-scale regression runs exercise the exact same
    simulator logic without paying filesystem/CSV parse overhead per cycle.
    """
    rng = random.Random(seed)
    start = start or datetime(2026, 8, 25, 11, 0, tzinfo=timezone.utc)
    if start.tzinfo is None:
        raise ValueError("simulator start must be timezone-aware")
    phases = [
        ("PRE_RINSE", 8),
        ("CAUSTIC", 24 if scenario == "context_long_run_response" else 22),
        ("INTERMEDIATE_RINSE", 7),
        ("ACID", 10),
        ("FINAL_RINSE", 16 if scenario == "excessive_rinse" else (13 if scenario == "context_long_run_response" else 9)),
    ]

    rows: list[dict] = []
    minute = 0
    for phase, duration in phases:
        for i in range(duration * 6):  # 10-second samples
            ts = start + timedelta(seconds=(minute * 60 + i * 10))
            if phase == "PRE_RINSE":
                temp = 30 + rng.gauss(0, 0.4)
                cond = max(1.0, 18 * math.exp(-i / 20) + rng.gauss(0, 0.2))
                flow = 415 + rng.gauss(0, 4)
            elif phase == "CAUSTIC":
                temp = 74.5 + rng.gauss(0, 0.5)
                cond = 45 + rng.gauss(0, 0.5)
                flow = 420 + rng.gauss(0, 4)
                if scenario == "low_temp" and 24 <= i <= 70:
                    temp = 68.5 + rng.gauss(0, 0.3)
                if scenario == "low_flow" and 30 <= i <= 90:
                    flow = 345 + rng.gauss(0, 3)
                if scenario == "compliant_low_flow":
                    # Deliberately unusual versus the normal ~420 L/min fingerprint,
                    # while remaining above the bundled demo recipe's 380 L/min minimum.
                    flow = 394 + rng.gauss(0, 1.5)
                if scenario == "profile_shift":
                    # Same-ish phase median as normal, but a sustained high-then-low
                    # hydraulic shape. This is designed to test profile intelligence.
                    half = max(1, duration * 3)
                    if i < half:
                        flow = 449 + rng.gauss(0, 1.5)
                    else:
                        flow = 391 + rng.gauss(0, 1.5)
                if scenario == "sensor_freeze" and 30 <= i <= 90:
                    flow = 402.0
            elif phase == "INTERMEDIATE_RINSE":
                temp = 40 + rng.gauss(0, 0.5)
                cond = max(1.1, 35 * math.exp(-i / 14) + rng.gauss(0, 0.15))
                flow = 418 + rng.gauss(0, 4)
            elif phase == "ACID":
                temp = 63 + rng.gauss(0, 0.5)
                cond = 20 + rng.gauss(0, 0.4)
                flow = 416 + rng.gauss(0, 4)
            else:
                temp = 28 + rng.gauss(0, 0.5)
                cond = max(0.8, 13 * math.exp(-i / 21) + rng.gauss(0, 0.1))
                # Known-answer simulator scenarios other than an explicit future
                # incomplete-rinse fault must finish with a deterministic compliant
                # endpoint hold. Random noise should exercise behavior statistics,
                # not silently relabel a nominal fixture as an endpoint failure.
                if scenario != "incomplete_rinse" and i >= duration * 6 - 4:
                    cond = min(cond, 1.2)
                flow = 417 + rng.gauss(0, 4)

            pressure = 2.8 + (420 - flow) * 0.006 + rng.gauss(0, 0.02)

            # Utility/resource meters are deliberately separate from process-loop return
            # flow. Return flow is mostly recirculation and must never be counted as
            # fresh-water consumption. These values are simulator-only development data.
            fresh_water_flow = (415 + rng.gauss(0, 3)) if phase in {"PRE_RINSE", "INTERMEDIATE_RINSE", "FINAL_RINSE"} else 0.0
            wastewater_flow = max(0.0, fresh_water_flow + rng.gauss(0, 1.0))
            electric_power = 8.0 + rng.gauss(0, 0.08)
            thermal_power = (150.0 + rng.gauss(0, 1.0)) if phase == "CAUSTIC" else ((90.0 + rng.gauss(0, 0.8)) if phase == "ACID" else 0.0)
            caustic_dose = (6.0 + rng.gauss(0, 0.05)) if phase == "CAUSTIC" and i < 12 else 0.0
            acid_dose = (3.0 + rng.gauss(0, 0.04)) if phase == "ACID" and i < 12 else 0.0

            rows.append({
                "timestamp": ts.isoformat(),
                "asset": asset,
                "phase": phase,
                "return_temperature_c": round(temp, 3),
                "return_flow_lpm": round(flow, 3),
                "return_conductivity_mscm": round(cond, 3),
                "return_pressure_bar": round(pressure, 3),
                "fresh_water_flow_lpm": round(fresh_water_flow, 3),
                "wastewater_flow_lpm": round(wastewater_flow, 3),
                "electric_power_kw": round(electric_power, 3),
                "thermal_power_kw": round(thermal_power, 3),
                "caustic_dose_kg_min": round(max(0.0, caustic_dose), 4),
                "acid_dose_kg_min": round(max(0.0, acid_dose), 4),
            })
        minute += duration
    return rows


def generate_cycle(
    path: Path,
    scenario: str = "normal",
    seed: int = 7,
    *,
    start: datetime | None = None,
    asset: str = "HTST-01",
) -> Path:
    rows = generate_cycle_rows(scenario=scenario, seed=seed, start=start, asset=asset)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return path
