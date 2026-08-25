from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from app.behavior.features import extract_behavior_features
from app.reconstruction.service import load_normalized_jsonl

from .engine import ENGINE_VERSION, build_context_baseline, build_production_context, evaluate_context
from .models import ContextBaselineRequest, ContextPolicy, ProductionRun
from .store import ContextBaselineStore, ProductionRunStore


class ProductionContextService:
    def __init__(self, runtime_root: Path):
        self.runtime_root = runtime_root
        self.run_store = ProductionRunStore(runtime_root / "context" / "production_runs")
        self.baseline_store = ContextBaselineStore(runtime_root / "context" / "baselines")
        self.output_root = runtime_root / "context" / "evaluations"
        self.normalized_root = runtime_root / "normalized"
        self.reconstruction_root = runtime_root / "reconstructions"
        self.compliance_root = runtime_root / "compliance"
        self.output_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _latest_json(directory: Path, missing: str) -> tuple[Path, dict]:
        paths = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not paths:
            raise FileNotFoundError(missing)
        path = paths[-1]
        return path, json.loads(path.read_text(encoding="utf-8"))

    def save_production_run(self, run: ProductionRun) -> dict:
        return self.run_store.save(run)

    def list_production_runs(self, asset: str | None = None) -> list[dict]:
        return self.run_store.list(asset=asset)

    def _candidates(self, ingestion_id: str, request: ContextBaselineRequest) -> list[dict]:
        records_path = self.normalized_root / ingestion_id / "records.jsonl"
        if not records_path.exists():
            raise FileNotFoundError(f"Normalized ingestion {ingestion_id!r} was not found.")
        recon_path, recon = self._latest_json(self.reconstruction_root / ingestion_id, f"No reconstruction exists for ingestion {ingestion_id!r}.")
        compliance_path, compliance = self._latest_json(self.compliance_root / ingestion_id, f"No compliance analysis exists for ingestion {ingestion_id!r}.")
        points = load_normalized_jsonl(records_path)
        recon_cycles = {c["cycle_id"]: c for c in recon.get("result", {}).get("cycles", [])}
        all_runs = [ProductionRun.model_validate(r) for r in self.run_store.list(asset=request.asset)]
        out = []
        for cresult in compliance.get("cycles", []):
            cycle = recon_cycles.get(cresult["cycle_id"])
            if not cycle or cycle["asset"] != request.asset:
                continue
            recipe = cresult.get("recipe", {})
            if recipe.get("name") != request.recipe_name or recipe.get("revision") != request.recipe_revision:
                continue
            context = build_production_context(cycle, all_runs, policy=request.policy)
            eligible = cresult.get("overall_assessment") == "COMPLIANT" and cycle.get("reconstruction_mode") == "EXPLICIT"
            out.append({
                "ingestion_id": ingestion_id, "cycle_id": cycle["cycle_id"], "start_ts": cycle["start_ts"],
                "eligible": eligible,
                "eligibility_reason": "eligible" if eligible else cresult.get("overall_assessment"),
                "context": context,
                "cip_features": extract_behavior_features(cycle, points, profile_bins=8),
                "normalized_sha256": hashlib.sha256(records_path.read_bytes()).hexdigest(),
                "reconstruction_sha256": hashlib.sha256(recon_path.read_bytes()).hexdigest(),
                "compliance_sha256": hashlib.sha256(compliance_path.read_bytes()).hexdigest(),
            })
        return out

    def build_baseline(self, request: ContextBaselineRequest) -> dict:
        candidates = []
        for ingestion_id in request.ingestion_ids:
            candidates.extend(self._candidates(ingestion_id, request))
        baseline = build_context_baseline(
            name=request.name, revision=request.revision, asset=request.asset,
            recipe_name=request.recipe_name, recipe_revision=request.recipe_revision,
            candidates=candidates, policy=request.policy, description=request.description,
        )
        return self.baseline_store.save(baseline)

    def evaluate_ingestion(self, ingestion_id: str, *, baseline_name: str, baseline_revision: str) -> dict:
        records_path = self.normalized_root / ingestion_id / "records.jsonl"
        if not records_path.exists():
            raise FileNotFoundError(f"Normalized ingestion {ingestion_id!r} was not found.")
        recon_path, recon = self._latest_json(self.reconstruction_root / ingestion_id, f"No reconstruction exists for ingestion {ingestion_id!r}.")
        compliance_path, compliance = self._latest_json(self.compliance_root / ingestion_id, f"No compliance analysis exists for ingestion {ingestion_id!r}.")
        points = load_normalized_jsonl(records_path)
        recon_cycles = {c["cycle_id"]: c for c in recon.get("result", {}).get("cycles", [])}
        evaluations = []
        baseline_hashes = []
        all_runs = [ProductionRun.model_validate(r) for r in self.run_store.list()]
        for cresult in compliance.get("cycles", []):
            cycle = recon_cycles.get(cresult["cycle_id"])
            if not cycle:
                continue
            baseline = self.baseline_store.get(asset=cycle["asset"], name=baseline_name, revision=baseline_revision)
            recipe = cresult.get("recipe", {})
            if recipe.get("name") != baseline["recipe"]["name"] or recipe.get("revision") != baseline["recipe"]["revision"]:
                raise ValueError("current cycle recipe does not match selected L4 baseline recipe revision")
            context = build_production_context(cycle, all_runs, policy=ContextPolicy.model_validate(baseline["policy"]))
            features = extract_behavior_features(cycle, points, profile_bins=8)
            evaluations.append(evaluate_context(context, features, baseline, l2_assessment=cresult["overall_assessment"]))
            baseline_hashes.append(hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest())

        source_hash = hashlib.sha256(records_path.read_bytes()).hexdigest()
        recon_hash = hashlib.sha256(recon_path.read_bytes()).hexdigest()
        compliance_hash = hashlib.sha256(compliance_path.read_bytes()).hexdigest()
        run_lineage = hashlib.sha256(json.dumps(self.run_store.list(), sort_keys=True).encode()).hexdigest()
        key = hashlib.sha256(json.dumps({
            "engine_version": ENGINE_VERSION, "source": source_hash, "reconstruction": recon_hash,
            "compliance": compliance_hash, "baselines": baseline_hashes, "production_runs": run_lineage,
        }, sort_keys=True).encode()).hexdigest()[:20]
        target_dir = self.output_root / ingestion_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{ENGINE_VERSION}-{key}.json"
        if target.exists():
            artifact = json.loads(target.read_text(encoding="utf-8"))
            artifact["duplicate"] = True
            artifact["artifact_path"] = str(target)
            return artifact
        artifact = {
            "ingestion_id": ingestion_id, "engine": "cip-production-context-intelligence", "engine_version": ENGINE_VERSION,
            "cycles": evaluations,
            "lineage": {"normalized_sha256": source_hash, "reconstruction_sha256": recon_hash,
                        "compliance_sha256": compliance_hash, "production_run_store_sha256": run_lineage,
                        "baseline_sha256": baseline_hashes},
            "duplicate": False,
        }
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
        target.chmod(target.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        artifact["artifact_path"] = str(target)
        return artifact
