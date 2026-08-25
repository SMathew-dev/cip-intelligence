from __future__ import annotations

import json
import re
from pathlib import Path

from app.acquisition.models import AcquisitionJob, AcquisitionSource


_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_name(value: str) -> str:
    cleaned = _SAFE.sub("-", value.strip()).strip("-.")
    if not cleaned:
        raise ValueError("name must contain a safe character")
    return cleaned


class SourceStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, source: AcquisitionSource) -> Path:
        path = self.root / f"{safe_name(source.name)}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(source.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def load(self, name: str) -> AcquisitionSource:
        path = self.root / f"{safe_name(name)}.json"
        return AcquisitionSource.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[AcquisitionSource]:
        return [AcquisitionSource.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(self.root.glob("*.json"))]


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, job: AcquisitionJob) -> Path:
        path = self.root / f"{safe_name(job.id)}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(job.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def load(self, job_id: str) -> AcquisitionJob:
        path = self.root / f"{safe_name(job_id)}.json"
        return AcquisitionJob.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self, source_name: str | None = None) -> list[AcquisitionJob]:
        jobs = [AcquisitionJob.model_validate_json(p.read_text(encoding="utf-8")) for p in self.root.glob("*.json")]
        if source_name is not None:
            jobs = [j for j in jobs if j.source_name == source_name]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)
