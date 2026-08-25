"""Seeded M11 known-answer validation campaign.

This script intentionally exercises the same reconstruction, L2 compliance and
L3 behavioral engines used by the application.  It is synthetic regression
validation only; the resulting rates must not be represented as real-plant
accuracy.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

from app.behavior.engine import build_baseline, evaluate_behavior
from app.behavior.features import extract_behavior_features
from app.behavior.models import BehaviorPolicy
from app.compliance.engine import evaluate_cycle
from app.compliance.models import ValidatedRecipe
from app.reconstruction.engine import reconstruct_cycles
from app.reconstruction.models import SignalPoint
from app.simulator import generate_cycle_rows

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "validation"
OUT.mkdir(exist_ok=True)
RECIPE_FIXTURE = ValidatedRecipe.model_validate_json(
    (ROOT / "config/example_htst_validated_recipe_v7.json").read_text()
)
POLICY = BehaviorPolicy()

# Four independent synthetic circuits are used to make the release campaign a
# facility-scale regression exercise while keeping the process fixture itself
# controlled and reproducible. They intentionally share the same simulated HTST
# validation envelope; this is not a claim that unlike dairy equipment should
# share one recipe.
ASSETS = ["HTST-01", "HTST-02", "HTST-03", "HTST-04"]
BASELINE_PER_ASSET = 60
SCENARIO_COUNTS_PER_ASSET = {
    "normal": 180,
    "low_temp": 60,
    "low_flow": 60,
    "sensor_freeze": 40,
    "excessive_rinse": 60,
    "compliant_low_flow": 60,
    "profile_shift": 40,
}
EXPECTED_L2 = {
    "normal": "COMPLIANT",
    "low_temp": "PROCESS_DEVIATION",
    "low_flow": "PROCESS_DEVIATION",
    "sensor_freeze": "DATA_REVIEW_REQUIRED",
    "excessive_rinse": "COMPLIANT",
    "compliant_low_flow": "COMPLIANT",
    "profile_shift": "COMPLIANT",
}
EXPECTED_L3_ABNORMAL = {
    "normal": False,
    "excessive_rinse": True,
    "compliant_low_flow": True,
    "profile_shift": True,
}


def points_from_rows(rows: list[dict]) -> list[SignalPoint]:
    return [
        SignalPoint(
            ts=datetime.fromisoformat(r["timestamp"]),
            asset=r["asset"],
            return_temperature_c=float(r["return_temperature_c"]),
            return_flow_lpm=float(r["return_flow_lpm"]),
            return_conductivity_mscm=float(r["return_conductivity_mscm"]),
            return_pressure_bar=float(r["return_pressure_bar"]),
            explicit_phase=r["phase"],
        )
        for r in rows
    ]


def recipe_for(asset: str) -> ValidatedRecipe:
    return RECIPE_FIXTURE.model_copy(
        update={
            "asset": asset,
            "approval_ref": f"SIMULATED-VALIDATION-REF-{asset}-007",
        }
    )


def run() -> dict:
    start0 = RECIPE_FIXTURE.effective_from + timedelta(days=1)
    results: list[dict] = []
    baselines: dict[str, dict] = {}
    t0 = time.perf_counter()

    # Build a separate historical reference for each synthetic circuit.
    for asset_idx, asset in enumerate(ASSETS):
        recipe = recipe_for(asset)
        candidates = []
        asset_start = start0 + timedelta(days=asset_idx * 3)
        for i in range(BASELINE_PER_ASSET):
            raw = generate_cycle_rows(
                "normal",
                seed=10_000 + asset_idx * 1_000 + i,
                start=asset_start + timedelta(hours=2 * i),
                asset=asset,
            )
            pts = points_from_rows(raw)
            cyc = reconstruct_cycles(pts)["cycles"][0]
            comp = evaluate_cycle(cyc, pts, recipe)
            feat = extract_behavior_features(cyc, pts, profile_bins=POLICY.profile_bins)
            candidates.append(
                {
                    "ingestion_id": f"base-{asset}-{i}",
                    "cycle_id": cyc["cycle_id"],
                    "start_ts": cyc["start_ts"],
                    "eligible": comp["overall_assessment"] == "COMPLIANT",
                    "features": feat,
                }
            )
        baselines[asset] = build_baseline(
            name=f"M11 {asset} reference",
            revision="1",
            asset=asset,
            recipe_name=recipe.name,
            recipe_revision=recipe.revision,
            candidates=candidates,
            policy=POLICY,
            description="Synthetic M11 facility-scale validation baseline",
        )

    # Evaluation periods are strictly later than all baseline observations.
    eval_start = start0 + timedelta(days=60)
    global_idx = 0
    for asset_idx, asset in enumerate(ASSETS):
        recipe = recipe_for(asset)
        baseline = baselines[asset]
        asset_eval_start = eval_start + timedelta(days=asset_idx * 5)
        for scenario, count in SCENARIO_COUNTS_PER_ASSET.items():
            for j in range(count):
                raw = generate_cycle_rows(
                    scenario,
                    seed=20_000 + global_idx,
                    start=asset_eval_start + timedelta(hours=2 * global_idx),
                    asset=asset,
                )
                global_idx += 1
                pts = points_from_rows(raw)
                rec = reconstruct_cycles(pts)
                cyc = rec["cycles"][0]
                comp = evaluate_cycle(cyc, pts, recipe)
                feat = extract_behavior_features(cyc, pts, profile_bins=POLICY.profile_bins)
                beh = evaluate_behavior(feat, baseline, l2_assessment=comp["overall_assessment"])
                results.append(
                    {
                        "asset": asset,
                        "scenario": scenario,
                        "cycle_id": cyc["cycle_id"],
                        "reconstruction_status": cyc["completeness"],
                        "l2": comp["overall_assessment"],
                        "l3": beh["behavioral_assessment"],
                    }
                )

    elapsed = time.perf_counter() - t0
    total_baseline = sum(b["training_cycle_count"] for b in baselines.values())
    total_cycles = total_baseline + len(results)

    summary: dict[str, dict] = {}
    for scenario in SCENARIO_COUNTS_PER_ASSET:
        rs = [r for r in results if r["scenario"] == scenario]
        item = {
            "n": len(rs),
            "l2": dict(Counter(r["l2"] for r in rs)),
            "l3": dict(Counter(r["l3"] for r in rs)),
            "l2_expected_rate": sum(r["l2"] == EXPECTED_L2[scenario] for r in rs) / len(rs),
        }
        if scenario in EXPECTED_L3_ABNORMAL:
            target = EXPECTED_L3_ABNORMAL[scenario]
            item["l3_expected_rate"] = sum(
                ((r["l3"] in {"UNUSUAL", "HIGHLY_UNUSUAL"}) == target) for r in rs
            ) / len(rs)
        summary[scenario] = item

    normal_rows = [r for r in results if r["scenario"] == "normal"]
    behavioral_fault_rows = [
        r for r in results
        if r["scenario"] in {"excessive_rinse", "compliant_low_flow", "profile_shift"}
    ]
    metrics = {
        "synthetic_assets": len(ASSETS),
        "generated_evaluation_cycles": len(results),
        "baseline_cycles": total_baseline,
        "total_cycles_exercised": total_cycles,
        "elapsed_seconds": round(elapsed, 3),
        "cycles_per_second": round(total_cycles / elapsed, 2),
        "l2_overall_expected_rate": sum(
            r["l2"] == EXPECTED_L2[r["scenario"]] for r in results
        ) / len(results),
        "normal_l3_false_alarm_rate": sum(
            r["l3"] in {"UNUSUAL", "HIGHLY_UNUSUAL"} for r in normal_rows
        ) / len(normal_rows),
        "behavioral_fault_detection_rate": sum(
            r["l3"] in {"UNUSUAL", "HIGHLY_UNUSUAL"} for r in behavioral_fault_rows
        ) / len(behavioral_fault_rows),
    }

    per_asset = {}
    for asset in ASSETS:
        ars = [r for r in results if r["asset"] == asset]
        normals = [r for r in ars if r["scenario"] == "normal"]
        faults = [
            r for r in ars
            if r["scenario"] in {"excessive_rinse", "compliant_low_flow", "profile_shift"}
        ]
        per_asset[asset] = {
            "evaluation_cycles": len(ars),
            "baseline_cycles": baselines[asset]["training_cycle_count"],
            "baseline_maturity": baselines[asset]["baseline_maturity"],
            "l2_expected_rate": sum(r["l2"] == EXPECTED_L2[r["scenario"]] for r in ars) / len(ars),
            "normal_l3_false_alarm_rate": sum(
                r["l3"] in {"UNUSUAL", "HIGHLY_UNUSUAL"} for r in normals
            ) / len(normals),
            "behavioral_fault_detection_rate": sum(
                r["l3"] in {"UNUSUAL", "HIGHLY_UNUSUAL"} for r in faults
            ) / len(faults),
        }

    payload = {
        "metrics": metrics,
        "scenario_summary": summary,
        "per_asset": per_asset,
        "baseline": {
            "assets": len(ASSETS),
            "training_cycle_count_total": total_baseline,
            "training_cycle_count_per_asset": BASELINE_PER_ASSET,
            "maturity": sorted({b["baseline_maturity"] for b in baselines.values()}),
        },
        "limitations": [
            "Synthetic known-answer regression only; not real-plant accuracy.",
            "All four simulated circuits use the same development-only HTST recipe envelope.",
            "Large-scale campaign focuses on reconstruction, L2 compliance, and L3 behavior; L0/L4/L5/L6 contracts are covered by the application regression suite.",
        ],
    }

    (OUT / "m11-validation-results.json").write_text(json.dumps(payload, indent=2))
    with (OUT / "m11-cycle-results.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=results[0].keys())
        w.writeheader()
        w.writerows(results)

    print(json.dumps({"metrics": metrics, "summary": summary, "per_asset": per_asset}, indent=2))
    return payload


if __name__ == "__main__":
    run()
