from __future__ import annotations

import csv
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.reconstruction.engine import reconstruct_cycles
from app.reconstruction.models import SignalPoint
from app.simulator import generate_cycle


def _points_for_scenario(scenario: str) -> tuple[list[dict], list[SignalPoint]]:
    allowed = {
        "normal",
        "low_temp",
        "low_flow",
        "sensor_freeze",
        "excessive_rinse",
        "compliant_low_flow",
        "profile_shift",
        "context_long_run_response",
    }
    if scenario not in allowed:
        raise ValueError(f"scenario must be one of {sorted(allowed)}")

    with NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        path = Path(tmp.name)
    generate_cycle(path, scenario=scenario)
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    points = [
        SignalPoint(
            ts=__import__("datetime").datetime.fromisoformat(r["timestamp"]),
            asset=r["asset"],
            return_temperature_c=float(r["return_temperature_c"]),
            return_flow_lpm=float(r["return_flow_lpm"]),
            return_conductivity_mscm=float(r["return_conductivity_mscm"]),
            return_pressure_bar=float(r["return_pressure_bar"]),
            explicit_phase=r["phase"],
        )
        for r in rows
    ]
    return rows, points


def demo_timeseries(scenario: str) -> dict:
    rows, points = _points_for_scenario(scenario)
    reconstruction = reconstruct_cycles(points)
    cycle = reconstruction["cycles"][0]
    start = points[0].ts

    samples = []
    for row, point in zip(rows, points):
        samples.append({
            "t_seconds": round((point.ts - start).total_seconds(), 3),
            "ts": point.ts.isoformat(),
            "phase": row["phase"],
            "temperature_c": point.return_temperature_c,
            "flow_lpm": point.return_flow_lpm,
            "conductivity_mscm": point.return_conductivity_mscm,
            "pressure_bar": point.return_pressure_bar,
        })

    phases = [
        {
            "phase": p["phase"],
            "start_seconds": round((__import__("datetime").datetime.fromisoformat(p["start_ts"]) - start).total_seconds(), 3),
            "end_seconds": round((__import__("datetime").datetime.fromisoformat(p["end_ts"]) - start).total_seconds() + 10.0, 3),
            "duration_seconds": p["duration_seconds"],
            "confidence": p["confidence"],
            "evidence_source": p["evidence_source"],
        }
        for p in cycle["phases"]
    ]

    return {
        "scenario": scenario,
        "asset": cycle["asset"],
        "cycle_id": cycle["cycle_id"],
        "start_ts": cycle["start_ts"],
        "end_ts": cycle["end_ts"],
        "duration_seconds": cycle["duration_seconds"],
        "samples": samples,
        "phases": phases,
        "simulator_only": True,
    }


def demo_overview() -> dict:
    # M9 presentation fixture. Every row is explicitly simulator/demo data; the
    # production backend will replace this with persisted asset/cycle queries.
    assets = [
        {
            "asset": "HTST-01",
            "type": "Pasteurizer",
            "area": "Fluid Processing",
            "assessment": "COMPLIANT",
            "behavior": "NORMAL",
            "data_confidence": 0.99,
            "last_cip_minutes": 56,
            "open_findings": 0,
            "resource_index": 0.97,
        },
        {
            "asset": "HTST-02",
            "type": "Pasteurizer",
            "area": "Fluid Processing",
            "assessment": "COMPLIANT",
            "behavior": "HIGHLY_UNUSUAL",
            "data_confidence": 0.98,
            "last_cip_minutes": 63,
            "open_findings": 1,
            "resource_index": 0.72,
        },
        {
            "asset": "VAT-04",
            "type": "Cheese Vat",
            "area": "Cheese Make",
            "assessment": "PROCESS_DEVIATION",
            "behavior": "HIGHLY_UNUSUAL",
            "data_confidence": 0.97,
            "last_cip_minutes": 58,
            "open_findings": 2,
            "resource_index": 0.88,
        },
        {
            "asset": "SILO-07",
            "type": "Raw Milk Silo",
            "area": "Receiving",
            "assessment": "COMPLIANT",
            "behavior": "NORMAL",
            "data_confidence": 0.94,
            "last_cip_minutes": 49,
            "open_findings": 0,
            "resource_index": 0.95,
        },
        {
            "asset": "UF-01",
            "type": "UF System",
            "area": "Membrane Hall",
            "assessment": "DATA_REVIEW_REQUIRED",
            "behavior": "NOT_EVALUABLE",
            "data_confidence": 0.61,
            "last_cip_minutes": 71,
            "open_findings": 1,
            "resource_index": None,
        },
    ]

    recent_cycles = [
        {"cycle_id": "CIP-18442", "asset": "HTST-01", "ago": "18 min ago", "assessment": "COMPLIANT", "behavior": "NORMAL", "duration_min": 56, "confidence": 0.99},
        {"cycle_id": "CIP-18441", "asset": "HTST-02", "ago": "1 h ago", "assessment": "COMPLIANT", "behavior": "HIGHLY_UNUSUAL", "duration_min": 63, "confidence": 0.98},
        {"cycle_id": "CIP-18440", "asset": "VAT-04", "ago": "2 h ago", "assessment": "PROCESS_DEVIATION", "behavior": "HIGHLY_UNUSUAL", "duration_min": 58, "confidence": 0.97},
        {"cycle_id": "CIP-18439", "asset": "SILO-07", "ago": "3 h ago", "assessment": "COMPLIANT", "behavior": "NORMAL", "duration_min": 49, "confidence": 0.94},
        {"cycle_id": "CIP-18438", "asset": "UF-01", "ago": "5 h ago", "assessment": "DATA_REVIEW_REQUIRED", "behavior": "NOT_EVALUABLE", "duration_min": 71, "confidence": 0.61},
    ]

    return {
        "plant": {
            "name": "CIP Intelligence Demo Dairy",
            "site": "Processing Campus A",
            "mode": "SIMULATED DATA",
            "control_boundary": "READ ONLY",
        },
        "summary": {
            "cycles_24h": 47,
            "compliant": 43,
            "process_deviations": 2,
            "data_review": 2,
            "behavioral_alerts": 3,
            "open_investigations": 3,
            "optimization_candidates": 1,
            "measured_water_m3": 468.2,
        },
        "assets": assets,
        "recent_cycles": recent_cycles,
        "attention": [
            {
                "severity": "HIGH",
                "title": "Validated flow requirement not achieved",
                "asset": "VAT-04",
                "detail": "Return flow remained below the approved minimum during required caustic exposure.",
                "action": "Review cycle",
            },
            {
                "severity": "MEDIUM",
                "title": "Compliant cycle, abnormal hydraulic profile",
                "asset": "HTST-02",
                "detail": "Flow profile differs materially from 30 eligible historical cycles despite L2 compliance.",
                "action": "Open evidence",
            },
            {
                "severity": "DATA",
                "title": "Flow evidence unavailable",
                "asset": "UF-01",
                "detail": "Return-flow signal appears flatlined; hydraulic conclusions are withheld.",
                "action": "Inspect data health",
            },
        ],
        "simulator_only": True,
    }


def demo_data_health() -> dict:
    sensors = [
        {"tag": "TT_420_RET", "concept": "Return temperature", "asset": "HTST-01", "coverage": 0.998, "status": "GOOD", "issue": None, "last_seen": "4 s ago"},
        {"tag": "FIT_214", "concept": "Return flow", "asset": "HTST-01", "coverage": 0.996, "status": "GOOD", "issue": None, "last_seen": "4 s ago"},
        {"tag": "AIT_104", "concept": "Return conductivity", "asset": "HTST-01", "coverage": 1.0, "status": "GOOD", "issue": None, "last_seen": "4 s ago"},
        {"tag": "PIT_311", "concept": "Return pressure", "asset": "HTST-01", "coverage": 0.992, "status": "GOOD", "issue": None, "last_seen": "4 s ago"},
        {"tag": "FIT_509", "concept": "Return flow", "asset": "UF-01", "coverage": 0.844, "status": "LOW", "issue": "Partial flatline detected", "last_seen": "38 min ago"},
        {"tag": "CIP_STEP_07", "concept": "CIP phase", "asset": "SILO-07", "coverage": 0.963, "status": "WARNING", "issue": "Short timestamp gap", "last_seen": "6 s ago"},
    ]
    return {
        "overall_score": 0.93,
        "trusted_signals": 38,
        "warning_signals": 2,
        "blocked_signals": 1,
        "sensors": sensors,
        "mapping_revision": "Demo-Plant-Mapping Rev 12",
        "last_ingestion": "11 s ago",
        "simulator_only": True,
    }
