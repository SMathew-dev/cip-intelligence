from __future__ import annotations

import csv
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.behavior.features import extract_behavior_features
from app.compliance.engine import evaluate_cycle
from app.compliance.models import ValidatedRecipe
from app.reconstruction.engine import reconstruct_cycles
from app.reconstruction.models import SignalPoint
from app.simulator import generate_cycle

from .engine import build_context_baseline, build_production_context, evaluate_context
from .models import ContextPolicy, ProductionRun, ProductionRunMetrics


def _points(path: Path) -> list[SignalPoint]:
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    from datetime import datetime
    return [SignalPoint(
        ts=datetime.fromisoformat(r["timestamp"]), asset=r["asset"],
        return_temperature_c=float(r["return_temperature_c"]), return_flow_lpm=float(r["return_flow_lpm"]),
        return_conductivity_mscm=float(r["return_conductivity_mscm"]), return_pressure_bar=float(r["return_pressure_bar"]),
        explicit_phase=r["phase"],
    ) for r in rows]


def _run(run_id: str, cip_start, *, long_context: bool) -> ProductionRun:
    hours = 12 if long_context else 6
    end = cip_start - timedelta(minutes=15)
    return ProductionRun(
        run_id=run_id, asset="HTST-01", product_code="MILK-A", product_family="MILK",
        batch_ref=f"B-{run_id}", start_ts=end - timedelta(hours=hours), end_ts=end,
        source_type="SIMULATOR", source_ref=f"sim://{run_id}",
        metrics=ProductionRunMetrics(
            average_throughput_lph=10500 if long_context else 10000,
            fat_pct=3.25, protein_pct=3.15, total_solids_pct=12.4,
            process_temperature_avg_c=74.2, process_temperature_max_c=75.0,
            shutdown_minutes=14 if long_context else 2,
            pressure_drop_start_bar=0.50, pressure_drop_end_bar=0.94 if long_context else 0.62,
            normalized_heat_transfer_start=1.0, normalized_heat_transfer_end=0.86 if long_context else 0.97,
        ),
    )


def demo_context(repo_root: Path, scenario: str) -> dict:
    if scenario not in {"normal", "long_run_response", "unexpected_after_short_run"}:
        raise ValueError("context demo scenario is unsupported")
    recipe = ValidatedRecipe.model_validate_json(
        (repo_root / "config" / "example_htst_validated_recipe_v7.json").read_text(encoding="utf-8")
    )
    policy = ContextPolicy(
        minimum_training_cycles=20, minimum_comparable_cycles=8,
        maximum_neighbors=10, minimum_shared_context_features=3, max_context_distance=2.5,
    )
    candidates = []
    for i in range(10):
        for long_context, day_offset in ((False, i + 1), (True, i + 20)):
            with NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
                path = Path(tmp.name)
            start = recipe.effective_from + timedelta(days=day_offset)
            cip_scenario = "context_long_run_response" if long_context else "normal"
            generate_cycle(path, scenario=cip_scenario, seed=1000 + day_offset, start=start)
            pts = _points(path)
            cycle = reconstruct_cycles(pts)["cycles"][0]
            compliance = evaluate_cycle(cycle, pts, recipe)
            context = build_production_context(cycle, [_run(f"history-{day_offset}", start, long_context=long_context)], policy=policy)
            candidates.append({
                "ingestion_id": f"demo-context-{day_offset}", "cycle_id": cycle["cycle_id"], "start_ts": cycle["start_ts"],
                "eligible": compliance["overall_assessment"] == "COMPLIANT",
                "context": context, "cip_features": extract_behavior_features(cycle, pts, profile_bins=8),
            })
    baseline = build_context_baseline(
        name="HTST-01-production-context", revision="demo-1", asset="HTST-01",
        recipe_name=recipe.name, recipe_revision=recipe.revision, candidates=candidates, policy=policy,
        description="Simulator-only L4 context baseline with short-run and long-run campaigns.",
    )

    if scenario == "normal":
        cip_scenario, long_context = "normal", False
    elif scenario == "long_run_response":
        cip_scenario, long_context = "context_long_run_response", True
    else:
        cip_scenario, long_context = "context_long_run_response", False

    start = recipe.effective_from + timedelta(days=80)
    with NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        path = Path(tmp.name)
    generate_cycle(path, scenario=cip_scenario, seed=777, start=start)
    pts = _points(path)
    cycle = reconstruct_cycles(pts)["cycles"][0]
    compliance = evaluate_cycle(cycle, pts, recipe)
    context = build_production_context(cycle, [_run(f"current-{scenario}", start, long_context=long_context)], policy=policy)
    features = extract_behavior_features(cycle, pts, profile_bins=8)
    return {
        "scenario": scenario,
        "l2_compliance": compliance["overall_assessment"],
        "production_context": context,
        "l4": evaluate_context(context, features, baseline, l2_assessment=compliance["overall_assessment"]),
        "demo_boundary": "All production values are deterministic simulator fixtures and are not dairy-industry fouling thresholds.",
    }
