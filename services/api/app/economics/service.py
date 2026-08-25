from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from .engine import ENGINE_VERSION, build_resource_baseline, calculate_resources, evaluate_economics
from .models import CostProfile, ResourceBaselineRequest, ResourcePolicy
from .store import ImmutableJsonStore


class EconomicsService:
    def __init__(self, runtime_root: Path):
        self.runtime_root = runtime_root
        self.normalized_root = runtime_root / "normalized"
        self.reconstruction_root = runtime_root / "reconstructions"
        self.compliance_root = runtime_root / "compliance"
        self.store = ImmutableJsonStore(runtime_root / "economics")
        self.evaluation_root = runtime_root / "economics" / "evaluations"
        self.evaluation_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _latest_json(root: Path, label: str) -> tuple[Path, dict]:
        if not root.exists():
            raise FileNotFoundError(label)
        paths = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        if not paths:
            raise FileNotFoundError(label)
        path = paths[0]
        return path, json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _records(path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def save_cost_profile(self, profile: CostProfile) -> dict:
        payload = {**profile.model_dump(mode="json"), "engine_boundary": "plant-configured economics; no industry-default rates"}
        return self.store.save("cost_profiles", profile.name, profile.revision, payload)

    def build_baseline(self, request: ResourceBaselineRequest) -> dict:
        candidates = []
        for ingestion_id in request.ingestion_ids:
            records_path = self.normalized_root / ingestion_id / "records.jsonl"
            if not records_path.exists():
                raise FileNotFoundError(f"Normalized ingestion {ingestion_id!r} was not found.")
            _, recon = self._latest_json(self.reconstruction_root / ingestion_id, f"No reconstruction exists for {ingestion_id!r}.")
            _, comp = self._latest_json(self.compliance_root / ingestion_id, f"No compliance exists for {ingestion_id!r}.")
            cycles = {c["cycle_id"]: c for c in recon.get("result", {}).get("cycles", [])}
            records = self._records(records_path)
            for cresult in comp.get("cycles", []):
                cycle = cycles.get(cresult["cycle_id"])
                if not cycle or cycle.get("asset") != request.asset:
                    continue
                recipe = cresult.get("recipe", {})
                eligible = (
                    cresult.get("overall_assessment") == "COMPLIANT"
                    and recipe.get("name") == request.recipe_name
                    and recipe.get("revision") == request.recipe_revision
                )
                candidates.append({
                    "ingestion_id": ingestion_id,
                    "cycle_id": cycle["cycle_id"],
                    "start_ts": cycle["start_ts"],
                    "eligible": eligible,
                    "summary": calculate_resources(cycle, records, request.policy),
                })
        baseline = build_resource_baseline(
            name=request.name, revision=request.revision, asset=request.asset,
            recipe_name=request.recipe_name, recipe_revision=request.recipe_revision,
            candidates=candidates, policy=request.policy, description=request.description,
        )
        return self.store.save("baselines", request.name, request.revision, baseline)

    def evaluate_ingestion(self, ingestion_id: str, *, baseline_name: str, baseline_revision: str,
                           cost_profile_name: str, cost_profile_revision: str) -> dict:
        records_path = self.normalized_root / ingestion_id / "records.jsonl"
        if not records_path.exists():
            raise FileNotFoundError(f"Normalized ingestion {ingestion_id!r} was not found.")
        _, recon = self._latest_json(self.reconstruction_root / ingestion_id, f"No reconstruction exists for {ingestion_id!r}.")
        _, comp = self._latest_json(self.compliance_root / ingestion_id, f"No compliance exists for {ingestion_id!r}.")
        baseline = self.store.get("baselines", baseline_name, baseline_revision)
        cost_profile = CostProfile.model_validate(self.store.get("cost_profiles", cost_profile_name, cost_profile_revision))
        cycles = {c["cycle_id"]: c for c in recon.get("result", {}).get("cycles", [])}
        records = self._records(records_path)
        evaluations = []
        for cresult in comp.get("cycles", []):
            cycle = cycles.get(cresult["cycle_id"])
            if not cycle:
                continue
            recipe = cresult.get("recipe", {})
            if recipe.get("name") != baseline["recipe"]["name"] or recipe.get("revision") != baseline["recipe"]["revision"]:
                raise ValueError("resource baseline recipe revision does not match evaluated cycle")
            summary = calculate_resources(cycle, records, ResourcePolicy.model_validate(baseline["policy"]))
            economics = evaluate_economics(summary, baseline, cost_profile, l2_assessment=cresult.get("overall_assessment", "UNKNOWN"))
            evaluations.append({"l2_assessment": cresult.get("overall_assessment"), "resource_summary": summary, "economics": economics})

        lineage = {
            "ingestion_id": ingestion_id,
            "normalized_sha256": hashlib.sha256(records_path.read_bytes()).hexdigest(),
            "baseline_sha256": hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest(),
            "cost_profile_sha256": hashlib.sha256(json.dumps(cost_profile.model_dump(mode="json"), sort_keys=True).encode()).hexdigest(),
            "engine_version": ENGINE_VERSION,
        }
        artifact_id = hashlib.sha256(json.dumps(lineage, sort_keys=True).encode()).hexdigest()[:24]
        path = self.evaluation_root / ingestion_id / f"{artifact_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        artifact = {"artifact_id": artifact_id, "lineage": lineage, "cycles": evaluations}
        encoded = json.dumps(artifact, indent=2, sort_keys=True, default=str)
        if path.exists():
            return {"duplicate": True, "artifact_path": str(path), "result": artifact}
        path.write_text(encoded, encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return {"duplicate": False, "artifact_path": str(path), "result": artifact}
