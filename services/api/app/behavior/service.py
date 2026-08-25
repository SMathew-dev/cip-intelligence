from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from app.reconstruction.io import load_normalized_jsonl

from .engine import ENGINE_VERSION, build_baseline, evaluate_behavior
from .features import extract_behavior_features
from .models import BehaviorBaselineRequest
from .store import BehaviorBaselineStore


class BehaviorService:
    def __init__(self, runtime_root: Path):
        self.runtime_root = runtime_root
        self.normalized_root = runtime_root / "normalized"
        self.reconstruction_root = runtime_root / "reconstructions"
        self.compliance_root = runtime_root / "compliance"
        self.baseline_store = BehaviorBaselineStore(runtime_root / "behavior" / "baselines")
        self.output_root = runtime_root / "behavior" / "evaluations"
        self.output_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _latest_json(root: Path, label: str) -> tuple[Path, dict]:
        if not root.exists():
            raise FileNotFoundError(label)
        paths = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        if not paths:
            raise FileNotFoundError(label)
        path = paths[0]
        return path, json.loads(path.read_text(encoding="utf-8"))

    def _cycle_candidates(self, ingestion_id: str, request: BehaviorBaselineRequest) -> list[dict]:
        records_path = self.normalized_root / ingestion_id / "records.jsonl"
        if not records_path.exists():
            raise FileNotFoundError(f"Normalized ingestion {ingestion_id!r} was not found.")
        recon_path, recon_artifact = self._latest_json(
            self.reconstruction_root / ingestion_id,
            f"No reconstruction exists for ingestion {ingestion_id!r}.",
        )
        compliance_path, compliance_artifact = self._latest_json(
            self.compliance_root / ingestion_id,
            f"No compliance analysis exists for ingestion {ingestion_id!r}.",
        )
        points = load_normalized_jsonl(records_path)
        recon_cycles = {c["cycle_id"]: c for c in recon_artifact.get("result", {}).get("cycles", [])}
        candidates = []
        for cresult in compliance_artifact.get("cycles", []):
            cycle_id = cresult["cycle_id"]
            cycle = recon_cycles.get(cycle_id)
            if cycle is None:
                continue
            eligible = True
            reason = "eligible"
            if cycle.get("asset") != request.asset:
                eligible, reason = False, "asset does not match requested baseline"
            elif cresult.get("recipe", {}).get("name") != request.recipe_name or cresult.get("recipe", {}).get("revision") != request.recipe_revision:
                eligible, reason = False, "recipe name/revision does not match requested baseline"
            elif cresult.get("overall_assessment") != "COMPLIANT":
                eligible, reason = False, f"L2 assessment is {cresult.get('overall_assessment')}"
            elif cycle.get("completeness") != "COMPLETE":
                eligible, reason = False, "reconstruction is partial"
            elif request.policy.require_explicit_reconstruction and cycle.get("reconstruction_mode") != "EXPLICIT":
                eligible, reason = False, "baseline policy requires explicit phase reconstruction"
            elif float(cycle.get("confidence", 0)) < request.policy.minimum_reconstruction_confidence:
                eligible, reason = False, "reconstruction confidence is below baseline policy"

            features = extract_behavior_features(cycle, points, profile_bins=request.policy.profile_bins) if eligible else None
            candidates.append({
                "ingestion_id": ingestion_id,
                "cycle_id": cycle_id,
                "start_ts": cycle.get("start_ts"),
                "eligible": eligible,
                "eligibility_reason": reason,
                "features": features,
                "normalized_sha256": hashlib.sha256(records_path.read_bytes()).hexdigest(),
                "reconstruction_sha256": hashlib.sha256(recon_path.read_bytes()).hexdigest(),
                "compliance_sha256": hashlib.sha256(compliance_path.read_bytes()).hexdigest(),
            })
        return candidates

    def build_baseline(self, request: BehaviorBaselineRequest) -> dict:
        candidates = []
        for ingestion_id in request.ingestion_ids:
            candidates.extend(self._cycle_candidates(ingestion_id, request))
        baseline = build_baseline(
            name=request.name,
            revision=request.revision,
            asset=request.asset,
            recipe_name=request.recipe_name,
            recipe_revision=request.recipe_revision,
            candidates=candidates,
            policy=request.policy,
            description=request.description,
        )
        return self.baseline_store.save(baseline)

    def evaluate_ingestion(self, ingestion_id: str, *, baseline_name: str, baseline_revision: str) -> dict:
        records_path = self.normalized_root / ingestion_id / "records.jsonl"
        if not records_path.exists():
            raise FileNotFoundError(f"Normalized ingestion {ingestion_id!r} was not found.")
        recon_path, recon_artifact = self._latest_json(
            self.reconstruction_root / ingestion_id,
            f"No reconstruction exists for ingestion {ingestion_id!r}.",
        )
        compliance_path, compliance_artifact = self._latest_json(
            self.compliance_root / ingestion_id,
            f"No compliance analysis exists for ingestion {ingestion_id!r}.",
        )
        points = load_normalized_jsonl(records_path)
        recon_cycles = {c["cycle_id"]: c for c in recon_artifact.get("result", {}).get("cycles", [])}

        evaluations = []
        baseline_hashes = []
        for cresult in compliance_artifact.get("cycles", []):
            cycle = recon_cycles.get(cresult["cycle_id"])
            if cycle is None:
                continue
            baseline = self.baseline_store.get(asset=cycle["asset"], name=baseline_name, revision=baseline_revision)
            if cresult.get("recipe", {}).get("name") != baseline["recipe"]["name"] or cresult.get("recipe", {}).get("revision") != baseline["recipe"]["revision"]:
                raise ValueError("current cycle recipe does not match the selected behavioral baseline recipe revision")
            policy = baseline["policy"]
            if policy.get("require_explicit_reconstruction", True) and cycle.get("reconstruction_mode") != "EXPLICIT":
                evaluations.append({
                    "cycle_id": cycle["cycle_id"],
                    "asset": cycle["asset"],
                    "behavioral_assessment": "NOT_EVALUABLE",
                    "l2_assessment": cresult["overall_assessment"],
                    "conclusion": "Behavioral analysis was withheld because this baseline requires explicit phase reconstruction.",
                    "deviations": [],
                    "profile_deviations": [],
                    "baseline": {"name": baseline_name, "revision": baseline_revision},
                })
            else:
                features = extract_behavior_features(cycle, points, profile_bins=int(policy["profile_bins"]))
                evaluations.append(evaluate_behavior(features, baseline, l2_assessment=cresult["overall_assessment"]))
            baseline_hashes.append(hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest())

        source_hash = hashlib.sha256(records_path.read_bytes()).hexdigest()
        recon_hash = hashlib.sha256(recon_path.read_bytes()).hexdigest()
        compliance_hash = hashlib.sha256(compliance_path.read_bytes()).hexdigest()
        key_payload = {
            "engine_version": ENGINE_VERSION,
            "source_hash": source_hash,
            "reconstruction_hash": recon_hash,
            "compliance_hash": compliance_hash,
            "baselines": baseline_hashes,
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

        artifact = {
            "ingestion_id": ingestion_id,
            "engine": "cip-behavioral-intelligence",
            "engine_version": ENGINE_VERSION,
            "cycles": evaluations,
            "lineage": {
                "normalized_sha256": source_hash,
                "reconstruction_sha256": recon_hash,
                "compliance_sha256": compliance_hash,
                "baseline_sha256": baseline_hashes,
            },
            "duplicate": False,
            "principle": "Behavioral intelligence cannot override deterministic L2 compliance.",
        }
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
        target.chmod(target.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        artifact["artifact_path"] = str(target)
        return artifact
