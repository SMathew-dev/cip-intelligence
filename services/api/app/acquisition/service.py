from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.acquisition.adapters.base import ReadOnlyAdapter
from app.acquisition.adapters.watched_folder import WatchedFolderAdapter
from app.acquisition.models import AcquisitionCandidate, AcquisitionJob, AcquisitionSource
from app.acquisition.store import JobStore, SourceStore
from app.ingestion.service import IngestionService


class UnsupportedAdapterError(NotImplementedError):
    pass


class AcquisitionService:
    """Coordinates durable, read-only acquisition jobs.

    The same ingestion engine is used for manual uploads and automated sources,
    so plant data does not follow a weaker validation path simply because it was
    automated.
    """

    def __init__(self, runtime_root: Path, ingestion_service: IngestionService | None = None):
        self.runtime_root = runtime_root
        acq_root = runtime_root / "acquisition"
        self.source_store = SourceStore(acq_root / "sources")
        self.job_store = JobStore(acq_root / "jobs")
        self.state_root = acq_root / "state"
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.ingestion = ingestion_service or IngestionService(runtime_root)

    def save_source(self, source: AcquisitionSource) -> dict:
        # Fail at configuration time if the mapping profile doesn't exist.
        self.ingestion.mapping_store.load(source.mapping_profile)
        path = self.source_store.save(source)
        return {
            "saved": True,
            "name": source.name,
            "adapter_type": source.adapter_type,
            "read_only": True,
            "path": str(path),
        }

    def _adapter(self, source: AcquisitionSource) -> ReadOnlyAdapter:
        if source.adapter_type == "watched_folder":
            return WatchedFolderAdapter(source)
        raise UnsupportedAdapterError(
            f"Adapter {source.adapter_type!r} is defined in Architecture v1 but is not implemented in Milestone 1B. "
            "CIP Intelligence will not simulate a live industrial connection."
        )

    @staticmethod
    def _candidate_fingerprint(candidate: AcquisitionCandidate) -> str:
        stable = {
            "source_name": candidate.source_name,
            "source_ref": candidate.source_ref,
            "size_bytes": candidate.metadata.get("size_bytes"),
            "mtime_ns": candidate.metadata.get("mtime_ns"),
        }
        return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()

    def _state_path(self, source_name: str) -> Path:
        import re
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", source_name.strip()).strip("-.")
        return self.state_root / f"{safe}.json"

    def _load_seen(self, source_name: str) -> dict[str, str]:
        path = self._state_path(source_name)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("seen", {})
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_seen(self, source_name: str, seen: dict[str, str]) -> None:
        path = self._state_path(source_name)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"seen": seen}, indent=2), encoding="utf-8")
        tmp.replace(path)

    def run_source(self, source_name: str, *, include_seen: bool = False) -> dict:
        source = self.source_store.load(source_name)
        if not source.enabled:
            return {"source": source_name, "status": "DISABLED", "jobs": []}
        adapter = self._adapter(source)
        candidates = adapter.discover()
        seen = self._load_seen(source_name)
        jobs: list[AcquisitionJob] = []

        for candidate in candidates:
            fingerprint = self._candidate_fingerprint(candidate)
            if not include_seen and fingerprint in seen:
                continue
            job = AcquisitionJob(
                id=str(uuid.uuid4()),
                source_name=source.name,
                source_ref=candidate.source_ref,
                filename=candidate.filename,
                mapping_profile=source.mapping_profile,
                metadata={"candidate_fingerprint": fingerprint, **candidate.metadata},
            )
            self.job_store.save(job)
            jobs.append(self._execute(job, source, adapter, candidate))
            # Only successful/skipped jobs are marked seen. A failure remains
            # discoverable so operators/engineering can correct configuration and retry.
            if jobs[-1].status in {"SUCCEEDED", "SKIPPED"}:
                seen[fingerprint] = jobs[-1].id
        self._save_seen(source_name, seen)
        return {
            "source": source.name,
            "adapter_type": source.adapter_type,
            "read_only": True,
            "discovered": len(candidates),
            "processed": len(jobs),
            "jobs": [j.model_dump(mode="json") for j in jobs],
        }

    def _execute(
        self,
        job: AcquisitionJob,
        source: AcquisitionSource,
        adapter: ReadOnlyAdapter,
        candidate: AcquisitionCandidate,
    ) -> AcquisitionJob:
        job.status = "RUNNING"
        job.attempts += 1
        job.updated_at = datetime.now(timezone.utc)
        self.job_store.save(job)
        try:
            content = adapter.read(candidate)
            result = self.ingestion.ingest(
                content,
                candidate.filename,
                source.mapping_profile,
                source_identity=f"acquisition:{source.name}:{candidate.source_ref}",
            )
            job.status = "SKIPPED" if result.get("duplicate") else "SUCCEEDED"
            job.ingestion_id = result.get("ingestion_id")
            job.duplicate = bool(result.get("duplicate"))
            job.error = None
        except Exception as exc:  # job boundary: persist failure for inspection/retry
            job.status = "FAILED"
            job.error = f"{type(exc).__name__}: {exc}"
        job.updated_at = datetime.now(timezone.utc)
        self.job_store.save(job)
        return job

    def retry_job(self, job_id: str) -> dict:
        prior = self.job_store.load(job_id)
        source = self.source_store.load(prior.source_name)
        adapter = self._adapter(source)
        candidate = AcquisitionCandidate(
            source_name=prior.source_name,
            source_ref=prior.source_ref,
            filename=prior.filename,
            metadata={k: v for k, v in prior.metadata.items() if k != "candidate_fingerprint"},
        )
        retry = AcquisitionJob(
            id=str(uuid.uuid4()),
            source_name=prior.source_name,
            source_ref=prior.source_ref,
            filename=prior.filename,
            mapping_profile=source.mapping_profile,
            metadata={"retry_of": prior.id, **prior.metadata},
        )
        result = self._execute(retry, source, adapter, candidate)
        if result.status in {"SUCCEEDED", "SKIPPED"}:
            seen = self._load_seen(source.name)
            fingerprint = prior.metadata.get("candidate_fingerprint") or self._candidate_fingerprint(candidate)
            seen[fingerprint] = result.id
            self._save_seen(source.name, seen)
        return result.model_dump(mode="json")
