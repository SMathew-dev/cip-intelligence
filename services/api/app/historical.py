from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import random
from statistics import median


ASSETS = {
    "HTST-01": {"type": "Pasteurizer", "base_flow": 414.0, "base_temp": 74.8, "base_duration": 56.0, "base_water": 9.7},
    "HTST-02": {"type": "Pasteurizer", "base_flow": 411.0, "base_temp": 74.5, "base_duration": 58.0, "base_water": 10.1},
    "VAT-04": {"type": "Cheese Vat", "base_flow": 402.0, "base_temp": 73.9, "base_duration": 57.0, "base_water": 11.2},
    "SILO-07": {"type": "Raw Milk Silo", "base_flow": 395.0, "base_temp": 73.5, "base_duration": 50.0, "base_water": 8.8},
    "UF-01": {"type": "UF System", "base_flow": 386.0, "base_temp": 72.9, "base_duration": 68.0, "base_water": 13.4},
}


def _slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    xbar = (n - 1) / 2
    ybar = sum(values) / n
    denom = sum((i - xbar) ** 2 for i in range(n))
    return 0.0 if not denom else sum((i - xbar) * (v - ybar) for i, v in enumerate(values)) / denom


def _history(days: int = 90) -> list[dict]:
    rng = random.Random(1101)
    end = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    rows: list[dict] = []
    cycle_number = 17000
    for day in range(days):
        ts = end - timedelta(days=days - 1 - day)
        progress = day / max(days - 1, 1)
        for asset, cfg in ASSETS.items():
            # Deterministic synthetic plant history. Different assets intentionally
            # demonstrate stable, drifting, deviation, and data-quality patterns.
            flow_shift = 0.0
            duration_shift = 0.0
            water_shift = 0.0
            temp_shift = 0.0
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
            if temp < 72.0 or flow < 380.0:
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
                "ts": (ts + timedelta(hours=(cycle_number % 17))).isoformat(),
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
    cutoff = datetime.fromisoformat(rows[-1]["ts"]) - timedelta(days=days)
    rows = [r for r in rows if datetime.fromisoformat(r["ts"]) >= cutoff]
    assets = []
    for asset, cfg in ASSETS.items():
        a = [r for r in rows if r["asset"] == asset]
        flows = [r["return_flow_lpm"] for r in a]
        temps = [r["caustic_temperature_c"] for r in a]
        durations = [r["duration_min"] for r in a]
        waters = [r["water_m3"] for r in a]
        deviations = sum(r["assessment"] == "PROCESS_DEVIATION" for r in a)
        reviews = sum(r["assessment"] == "DATA_REVIEW_REQUIRED" for r in a)
        unusual = sum(r["behavior"] in {"UNUSUAL", "HIGHLY_UNUSUAL"} for r in a)
        flow_change = _slope(flows) * max(len(flows) - 1, 1)
        duration_change = _slope(durations) * max(len(durations) - 1, 1)
        water_change = _slope(waters) * max(len(waters) - 1, 1)
        risk = min(100, round(deviations * 14 + reviews * 9 + unusual * 2.5 + max(0, -flow_change) * 1.1 + max(0, duration_change) * 3 + max(0, water_change) * 8))
        status = "STABLE" if risk < 25 else "WATCH" if risk < 55 else "ATTENTION"
        assets.append({
            "asset": asset,
            "asset_type": cfg["type"],
            "cycles": len(a),
            "status": status,
            "attention_score": risk,
            "median_flow_lpm": round(median(flows), 1),
            "flow_change_lpm": round(flow_change, 1),
            "median_temp_c": round(median(temps), 1),
            "median_duration_min": round(median(durations), 1),
            "duration_change_min": round(duration_change, 1),
            "total_water_m3": round(sum(waters), 1),
            "water_change_m3_per_cycle": round(water_change, 2),
            "process_deviations": deviations,
            "data_reviews": reviews,
            "unusual_cycles": unusual,
        })
    assets.sort(key=lambda x: x["attention_score"], reverse=True)
    total_water = sum(r["water_m3"] for r in rows)
    stable_water = sum(ASSETS[r["asset"]]["base_water"] for r in rows)
    avoidable = max(0.0, total_water - stable_water)
    return {
        "window_days": days,
        "generated_from": "deterministic synthetic multi-cycle history",
        "simulator_only": True,
        "summary": {
            "cycles": len(rows),
            "assets": len(ASSETS),
            "process_deviations": sum(r["assessment"] == "PROCESS_DEVIATION" for r in rows),
            "data_reviews": sum(r["assessment"] == "DATA_REVIEW_REQUIRED" for r in rows),
            "behavioral_alerts": sum(r["behavior"] in {"UNUSUAL", "HIGHLY_UNUSUAL"} for r in rows),
            "water_m3": round(total_water, 1),
            "estimated_excess_water_m3": round(avoidable, 1),
        },
        "asset_ranking": assets,
        "daily": rows,
        "interpretation": "Attention scores prioritize investigation only; they do not alter L2 compliance or authorize process changes.",
    }
