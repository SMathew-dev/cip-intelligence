from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
from statistics import median


# Simulator fixtures only. The thresholds below exist to create known-answer
# historical scenarios; they are not universal CIP recommendations. Real plant
# deployments must evaluate each asset against its approved recipe/specification.
ASSETS = {
    "HTST-01": {"type": "Pasteurizer", "base_flow": 414.0, "base_temp": 74.8, "base_duration": 56.0, "base_water": 9.7, "min_flow": 380.0, "min_temp": 72.0},
    "HTST-02": {"type": "Pasteurizer", "base_flow": 411.0, "base_temp": 74.5, "base_duration": 58.0, "base_water": 10.1, "min_flow": 380.0, "min_temp": 72.0},
    "VAT-04": {"type": "Cheese Vat", "base_flow": 402.0, "base_temp": 73.9, "base_duration": 57.0, "base_water": 11.2, "min_flow": 380.0, "min_temp": 72.0},
    "SILO-07": {"type": "Raw Milk Silo", "base_flow": 395.0, "base_temp": 73.5, "base_duration": 50.0, "base_water": 8.8, "min_flow": 360.0, "min_temp": 70.0},
    "UF-01": {"type": "UF System", "base_flow": 386.0, "base_temp": 72.9, "base_duration": 68.0, "base_water": 13.4, "min_flow": 365.0, "min_temp": 70.0},
}


def _slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    xbar = (n - 1) / 2
    ybar = sum(values) / n
    denominator = sum((i - xbar) ** 2 for i in range(n))
    if not denominator:
        return 0.0
    return sum((i - xbar) * (value - ybar) for i, value in enumerate(values)) / denominator


def _history(days: int = 90) -> list[dict]:
    rng = random.Random(1101)
    end = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    rows: list[dict] = []
    cycle_number = 17000

    for day in range(days):
        ts = end - timedelta(days=days - 1 - day)
        progress = day / max(days - 1, 1)

        for asset, cfg in ASSETS.items():
            flow_shift = 0.0
            duration_shift = 0.0
            water_shift = 0.0
            temp_shift = 0.0

            # Deliberately different known-answer patterns:
            # HTST-02 develops hydraulic/duration drift.
            # VAT-04 develops a late temperature deterioration.
            # UF-01 develops duration/water drift plus explicit data-review events.
            if asset == "HTST-02":
                flow_shift = -24.0 * max(0.0, (progress - 0.35) / 0.65)
                duration_shift = 5.5 * max(0.0, (progress - 0.45) / 0.55)
            elif asset == "VAT-04":
                temp_shift = -2.8 * max(0.0, (progress - 0.72) / 0.28)
            elif asset == "UF-01":
                duration_shift = 7.0 * progress
                water_shift = 2.2 * progress

            flow = cfg["base_flow"] + flow_shift + rng.gauss(0, 3.0)
            temp = cfg["base_temp"] + temp_shift + rng.gauss(0, 0.45)
            duration = cfg["base_duration"] + duration_shift + rng.gauss(0, 1.8)
            water = cfg["base_water"] + water_shift + rng.gauss(0, 0.35)
            final_rinse = 10.0 + max(0.0, duration - cfg["base_duration"]) * 0.65 + rng.gauss(0, 0.7)

            assessment = "COMPLIANT"
            behavior = "NORMAL"
            data_confidence = max(0.75, min(0.999, rng.gauss(0.975, 0.012)))

            # Demo compliance remains deterministic but uses fixture-specific
            # requirements instead of pretending one threshold fits every asset.
            if temp < cfg["min_temp"] or flow < cfg["min_flow"]:
                assessment = "PROCESS_DEVIATION"
                behavior = "HIGHLY_UNUSUAL"
            elif abs(flow - cfg["base_flow"]) > 16 or abs(duration - cfg["base_duration"]) > 6:
                behavior = "HIGHLY_UNUSUAL"
            elif abs(flow - cfg["base_flow"]) > 10 or abs(duration - cfg["base_duration"]) > 4:
                behavior = "UNUSUAL"

            if asset == "UF-01" and day in {71, 82, 87}:
                assessment = "DATA_REVIEW_REQUIRED"
                behavior = "NOT_EVALUABLE"
                data_confidence = 0.58 + rng.random() * 0.08

            rows.append({
                "cycle_id": f"CIP-{cycle_number}",
                "asset": asset,
                "asset_type": cfg["type"],
                "ts": (ts + timedelta(hours=cycle_number % 17)).isoformat(),
                "day": ts.date().isoformat(),
                "return_flow_lpm": round(flow, 2),
                "caustic_temperature_c": round(temp, 2),
                "duration_min": round(duration, 2),
                "final_rinse_min": round(final_rinse, 2),
                "water_m3": round(max(0, water), 3),
                "assessment": assessment,
                "behavior": behavior,
                "data_confidence": round(data_confidence, 3),
            })
            cycle_number += 1

    return rows


def historical_intelligence(days: int = 90) -> dict:
    days = max(30, min(days, 90))
    rows = _history(90)

    # Select complete plant-days so 30/60/90-day windows contain exactly
    # 30/60/90 observations per asset in this one-cycle-per-day fixture.
    available_days = sorted({row["day"] for row in rows})
    selected_days = set(available_days[-days:])
    rows = [row for row in rows if row["day"] in selected_days]

    assets: list[dict] = []
    for asset, cfg in ASSETS.items():
        history = [row for row in rows if row["asset"] == asset]
        count = len(history)
        flows = [row["return_flow_lpm"] for row in history]
        temps = [row["caustic_temperature_c"] for row in history]
        durations = [row["duration_min"] for row in history]
        waters = [row["water_m3"] for row in history]

        deviations = sum(row["assessment"] == "PROCESS_DEVIATION" for row in history)
        reviews = sum(row["assessment"] == "DATA_REVIEW_REQUIRED" for row in history)
        unusual = sum(row["behavior"] in {"UNUSUAL", "HIGHLY_UNUSUAL"} for row in history)

        flow_change = _slope(flows) * max(count - 1, 1)
        temp_change = _slope(temps) * max(count - 1, 1)
        duration_change = _slope(durations) * max(count - 1, 1)
        water_change = _slope(waters) * max(count - 1, 1)

        # Advisory prioritization only. Components are bounded to avoid a single
        # long history count dominating the score and to keep the explanation
        # traceable to observable rates/trends.
        risk = round(
            min(deviations / count, 1) * 55
            + (20 if deviations else 0)
            + min(reviews / count, 1) * 35
            + (12 if reviews else 0)
            + min(unusual / count, 1) * 25
            + min(max(-flow_change, 0) / 25, 1) * 28
            + min(max(-temp_change, 0) / 3, 1) * 25
            + min(max(duration_change, 0) / 8, 1) * 18
            + min(max(water_change, 0) / 2.5, 1) * 18
        )
        risk = min(100, risk)
        status = "STABLE" if risk < 25 else "WATCH" if risk < 50 else "ATTENTION"

        assets.append({
            "asset": asset,
            "asset_type": cfg["type"],
            "cycles": count,
            "status": status,
            "attention_score": risk,
            "median_flow_lpm": round(median(flows), 1),
            "flow_change_lpm": round(flow_change, 1),
            "median_temp_c": round(median(temps), 1),
            "temperature_change_c": round(temp_change, 1),
            "median_duration_min": round(median(durations), 1),
            "duration_change_min": round(duration_change, 1),
            "total_water_m3": round(sum(waters), 1),
            "water_change_m3_per_cycle": round(water_change, 2),
            "process_deviations": deviations,
            "data_reviews": reviews,
            "unusual_cycles": unusual,
        })

    assets.sort(key=lambda row: row["attention_score"], reverse=True)
    total_water = sum(row["water_m3"] for row in rows)
    excess_water = sum(max(0.0, row["water_m3"] - ASSETS[row["asset"]]["base_water"]) for row in rows)

    return {
        "window_days": days,
        "generated_from": "deterministic synthetic multi-cycle history",
        "simulator_only": True,
        "summary": {
            "cycles": len(rows),
            "assets": len(ASSETS),
            "process_deviations": sum(row["assessment"] == "PROCESS_DEVIATION" for row in rows),
            "data_reviews": sum(row["assessment"] == "DATA_REVIEW_REQUIRED" for row in rows),
            "behavioral_alerts": sum(row["behavior"] in {"UNUSUAL", "HIGHLY_UNUSUAL"} for row in rows),
            "water_m3": round(total_water, 1),
            "estimated_excess_water_m3": round(excess_water, 1),
        },
        "asset_ranking": assets,
        "daily": rows,
        "interpretation": "Attention scores prioritize investigation only; they do not alter L2 compliance or authorize process changes.",
    }
