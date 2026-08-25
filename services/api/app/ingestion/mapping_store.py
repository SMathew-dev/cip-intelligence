from __future__ import annotations

import json
import re
from pathlib import Path

from app.ingestion.models import MappingProfile


class MappingStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(name: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-.")
        if not cleaned:
            raise ValueError("mapping profile name must contain a safe character")
        return cleaned

    def save(self, profile: MappingProfile) -> Path:
        path = self.root / f"{self._safe_name(profile.name)}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def load(self, name: str) -> MappingProfile:
        path = self.root / f"{self._safe_name(name)}.json"
        return MappingProfile.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))
