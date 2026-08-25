from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import asdict
from pathlib import Path

from .engine import ReconstructionConfig, reconstruct_cycles
from .io import load_normalized_jsonl

RECONSTRUCTION_ENGINE_VERSION = "0.1.0"


class ReconstructionService:
    def __init__(self, runtime_root: Path):
        self.runtime_root = runtime_root
        self.normalized_root = runtime_root / "normalized"
        self.output_root = runtime_root / "reconstructions"
        self.output_root.mkdir(parents=True, exist_ok=True)

    def reconstruct_ingestion(
        self,
        ingestion_id: str,
        config: ReconstructionConfig | None = None,
    ) -> dict:
        records_path = self.normalized_root / ingestion_id / "records.jsonl"
        if not records_path.exists():
            raise FileNotFoundError(f"Normalized ingestion {ingestion_id!r} was not found.")

        cfg = config or ReconstructionConfig()
        records_bytes = records_path.read_bytes()
        normalized_sha256 = hashlib.sha256(records_bytes).hexdigest()
        config_dict = asdict(cfg)
        config_hash = hashlib.sha256(
            json.dumps(config_dict, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:12]
        analysis_key = f"{RECONSTRUCTION_ENGINE_VERSION}-{config_hash}-{normalized_sha256[:12]}"
        target_dir = self.output_root / ingestion_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{analysis_key}.json"

        if target_path.exists():
            saved = json.loads(target_path.read_text(encoding="utf-8"))
            saved["duplicate"] = True
            saved["artifact_path"] = str(target_path)
            return saved

        points = load_normalized_jsonl(records_path)
        result = reconstruct_cycles(points, cfg)
        artifact = {
            "ingestion_id": ingestion_id,
            "engine": "cycle-phase-reconstruction",
            "engine_version": RECONSTRUCTION_ENGINE_VERSION,
            "normalized_sha256": normalized_sha256,
            "config": config_dict,
            "result": result,
            "duplicate": False,
        }
        tmp_path = target_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, target_path)
        # Analysis artifacts are lineage records. Treat them as immutable and make a
        # new versioned artifact when engine/config/source data changes.
        target_path.chmod(target_path.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        artifact["artifact_path"] = str(target_path)
        return artifact
