from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.ingestion.models import MappingProfile
from app.ingestion.service import IngestionService
from app.reconstruction.service import ReconstructionService


class PlantOnboardingService:
    """Turn an engineer-confirmed plant export into a read-only analysis workspace.

    This service deliberately stops short of compliance, release, or optimization
    decisions. Those layers require plant-approved recipes and additional evidence.
    """

    def __init__(
        self,
        runtime_root: Path,
        ingestion_service: IngestionService,
        reconstruction_service: ReconstructionService,
    ) -> None:
        self.runtime_root = runtime_root
        self.ingestion_service = ingestion_service
        self.reconstruction_service = reconstruction_service
        self.workspace_root = runtime_root / "workspaces"
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def analyze(
        self,
        content: bytes,
        filename: str,
        profile: MappingProfile,
        *,
        confirmed: bool,
    ) -> dict:
        if not confirmed:
            raise ValueError(
                "Engineering confirmation is required before proposed equipment and tag mappings are used for analysis."
            )
        if not profile.mappings:
            raise ValueError("At least one engineer-confirmed signal mapping is required.")

        self.ingestion_service.save_mapping(profile)
        ingestion = self.ingestion_service.ingest(
            content,
            filename,
            profile.name,
            source_identity="engineer-confirmed-upload",
        )
        reconstruction = self.reconstruction_service.reconstruct_ingestion(ingestion["ingestion_id"])
        workspace = self._build_workspace(profile, ingestion, reconstruction)
        self._save_workspace(workspace)
        return workspace

    def load(self, workspace_id: str) -> dict:
        safe = workspace_id.strip()
        if not safe or "/" in safe or "\\" in safe or ".." in safe:
            raise FileNotFoundError("Workspace was not found.")
        path = self.workspace_root / f"{safe}.json"
        if not path.exists():
            raise FileNotFoundError(f"Workspace {workspace_id!r} was not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    def _build_workspace(self, profile: MappingProfile, ingestion: dict, reconstruction: dict) -> dict:
        records_path = Path(ingestion["normalized_object"])
        records = [
            json.loads(line)
            for line in records_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        assets: dict[str, dict] = defaultdict(
            lambda: {
                "concepts": set(),
                "source_columns": set(),
                "good_points": 0,
                "flagged_points": 0,
                "timestamps": set(),
            }
        )
        resource_concepts: set[str] = set()
        for record in records:
            asset = record.get("asset") or "UNASSIGNED"
            entry = assets[asset]
            entry["concepts"].add(record["concept"])
            entry["source_columns"].add(record["source_column"])
            entry["timestamps"].add(record["ts_utc"])
            if record.get("quality_code") == "GOOD":
                entry["good_points"] += 1
            else:
                entry["flagged_points"] += 1
            concept = record["concept"]
            if concept.startswith("cip.utility.") or concept.startswith("cip.chemical."):
                resource_concepts.add(concept)

        reconstruction_result = reconstruction.get("result", {})
        cycles = reconstruction_result.get("cycles", [])
        cycle_counts: dict[str, int] = defaultdict(int)
        for cycle in cycles:
            cycle_counts[cycle.get("asset") or "UNASSIGNED"] += 1

        asset_rows = []
        for asset, values in sorted(assets.items()):
            total = values["good_points"] + values["flagged_points"]
            asset_rows.append({
                "asset": asset,
                "signal_count": len(values["source_columns"]),
                "concept_count": len(values["concepts"]),
                "concepts": sorted(values["concepts"]),
                "sample_count": len(values["timestamps"]),
                "good_points": values["good_points"],
                "flagged_points": values["flagged_points"],
                "data_confidence": round(values["good_points"] / total, 4) if total else 0.0,
                "reconstructed_cycles": cycle_counts.get(asset, 0),
            })

        compact_cycles = [
            {
                "cycle_id": cycle.get("cycle_id"),
                "asset": cycle.get("asset"),
                "start_ts": cycle.get("start_ts"),
                "end_ts": cycle.get("end_ts"),
                "duration_seconds": cycle.get("duration_seconds"),
                "confidence": cycle.get("confidence"),
                "completeness": cycle.get("completeness"),
                "reconstruction_mode": cycle.get("reconstruction_mode"),
                "phase_count": len(cycle.get("phases", [])),
                "phases": [p.get("phase") for p in cycle.get("phases", [])],
            }
            for cycle in cycles
        ]

        ingestion_summary = ingestion.get("summary", {})
        cycle_count = int(reconstruction_result.get("cycle_count", len(compact_cycles)))
        reconstruction_issues = reconstruction_result.get("issues", [])
        mapped_concepts = sorted({r["concept"] for r in records})

        return {
            "workspace_id": ingestion["ingestion_id"],
            "mode": "UPLOADED_PLANT_DATA",
            "read_only": True,
            "plant": profile.plant,
            "source_system": profile.source_system,
            "timezone": profile.timezone,
            "mapping_profile": profile.name,
            "source": {
                "filename": Path(records_path).parent.name and ingestion.get("summary", {}).get("original_filename", None),
                "sha256": ingestion.get("sha256"),
                "duplicate": ingestion.get("duplicate", False),
            },
            "summary": {
                "source_rows": ingestion_summary.get("rows_in_source", 0),
                "normalized_points": ingestion_summary.get("normalized_points", 0),
                "good_points": ingestion_summary.get("good_points", 0),
                "data_coverage": ingestion_summary.get("data_coverage", 0.0),
                "high_severity_data_issues": ingestion_summary.get("high_severity_issue_count", 0),
                "asset_count": len(asset_rows),
                "reconstructed_cycles": cycle_count,
                "reconstruction_issues": len(reconstruction_issues),
                "mapped_concepts": len(mapped_concepts),
            },
            "assets": asset_rows,
            "cycles": compact_cycles,
            "data_issues": ingestion_summary.get("issues", [])[:100],
            "reconstruction_issues": reconstruction_issues[:100],
            "readiness": {
                "cycle_reconstruction": {
                    "status": "AVAILABLE" if cycle_count else "NEEDS_EVIDENCE",
                    "detail": (
                        f"{cycle_count} CIP cycle(s) reconstructed from confirmed mappings."
                        if cycle_count
                        else "No sufficiently supported CIP cycle could be reconstructed from the available evidence."
                    ),
                },
                "compliance": {
                    "status": "NEEDS_APPROVED_RECIPE",
                    "detail": "Upload or configure the plant-approved recipe revision before L2 compliance is evaluated.",
                },
                "resource_accounting": {
                    "status": "AVAILABLE" if resource_concepts else "NEEDS_DEDICATED_METERS",
                    "detail": (
                        f"Dedicated resource evidence detected: {', '.join(sorted(resource_concepts))}."
                        if resource_concepts
                        else "No dedicated utility or chemical measurement was confirmed; recirculating process flow is not counted as consumption."
                    ),
                },
            },
            "boundary": (
                "This workspace is read-only. Reconstruction is analytical evidence only; it does not prove cleanliness, "
                "authorize sanitation release, change a validated recipe, or write to PLC/HMI systems."
            ),
        }

    def _save_workspace(self, workspace: dict) -> None:
        target = self.workspace_root / f"{workspace['workspace_id']}.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(workspace, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(target)
