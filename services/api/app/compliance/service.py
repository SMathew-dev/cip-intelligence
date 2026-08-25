from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from app.reconstruction.io import load_normalized_jsonl

from .engine import ENGINE_VERSION, evaluate_cycle
from .models import ValidatedRecipe
from .store import RecipeStore


class ComplianceService:
    def __init__(self, runtime_root: Path):
        self.runtime_root = runtime_root
        self.normalized_root = runtime_root / "normalized"
        self.reconstruction_root = runtime_root / "reconstructions"
        self.output_root = runtime_root / "compliance"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.recipe_store = RecipeStore(runtime_root / "recipes")

    def save_recipe(self, recipe: ValidatedRecipe) -> dict:
        return self.recipe_store.save(recipe)

    def _latest_reconstruction(self, ingestion_id: str) -> tuple[Path, dict]:
        root = self.reconstruction_root / ingestion_id
        if not root.exists():
            raise FileNotFoundError(f"No reconstruction exists for ingestion {ingestion_id!r}.")
        paths = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        if not paths:
            raise FileNotFoundError(f"No reconstruction exists for ingestion {ingestion_id!r}.")
        path = paths[0]
        return path, json.loads(path.read_text(encoding="utf-8"))

    def evaluate_ingestion(self, ingestion_id: str, *, recipe_name: str | None = None) -> dict:
        records_path = self.normalized_root / ingestion_id / "records.jsonl"
        if not records_path.exists():
            raise FileNotFoundError(f"Normalized ingestion {ingestion_id!r} was not found.")
        recon_path, recon = self._latest_reconstruction(ingestion_id)
        cycles = recon.get("result", {}).get("cycles", [])
        if not cycles:
            raise ValueError("compliance cannot run because reconstruction established no CIP cycles")
        points = load_normalized_jsonl(records_path)

        evaluations = []
        recipe_fingerprints = []
        for cycle in cycles:
            start = __import__("datetime").datetime.fromisoformat(cycle["start_ts"].replace("Z", "+00:00"))
            recipe = self.recipe_store.select_effective(cycle["asset"], start, name=recipe_name)
            recipe_json = json.dumps(recipe.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            recipe_fingerprints.append(hashlib.sha256(recipe_json.encode()).hexdigest())
            evaluations.append(evaluate_cycle(cycle, points, recipe))

        source_hash = hashlib.sha256(records_path.read_bytes()).hexdigest()
        recon_hash = hashlib.sha256(recon_path.read_bytes()).hexdigest()
        key_payload = {
            "engine_version": ENGINE_VERSION,
            "source_hash": source_hash,
            "reconstruction_hash": recon_hash,
            "recipes": recipe_fingerprints,
        }
        key = hashlib.sha256(json.dumps(key_payload, sort_keys=True).encode()).hexdigest()[:20]
        target_dir = self.output_root / ingestion_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{ENGINE_VERSION}-{key}.json"
        if target.exists():
            artifact = json.loads(target.read_text(encoding="utf-8"))
            artifact["duplicate"] = True
            artifact["artifact_path"] = str(target)
            return artifact

        overall = "COMPLIANT"
        if any(x["overall_assessment"] == "PROCESS_DEVIATION" for x in evaluations):
            overall = "PROCESS_DEVIATION"
        elif any(x["overall_assessment"] == "DATA_REVIEW_REQUIRED" for x in evaluations):
            overall = "DATA_REVIEW_REQUIRED"

        artifact = {
            "ingestion_id": ingestion_id,
            "engine": "validated-cip-compliance",
            "engine_version": ENGINE_VERSION,
            "overall_assessment": overall,
            "cycles": evaluations,
            "lineage": {
                "normalized_sha256": source_hash,
                "reconstruction_sha256": recon_hash,
                "recipe_sha256": recipe_fingerprints,
            },
            "duplicate": False,
        }
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
        target.chmod(target.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        artifact["artifact_path"] = str(target)
        return artifact
