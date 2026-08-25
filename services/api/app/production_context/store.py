from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .models import ProductionRun


class ProductionRunStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe(value: str) -> str:
        clean = "".join(c if c.isalnum() or c in {"-", "_", "."} else "_" for c in value.strip())
        if not clean:
            raise ValueError("production run identifier produced an empty safe filename")
        return clean

    def _path(self, run_id: str) -> Path:
        return self.root / f"{self._safe(run_id)}.json"

    def save(self, run: ProductionRun) -> dict:
        target = self._path(run.run_id)
        payload = run.model_dump(mode="json")
        encoded = json.dumps(payload, indent=2, sort_keys=True)
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing == payload:
                return {"saved": False, "duplicate": True, "path": str(target), "run": payload}
            raise ValueError("production run IDs are immutable; create a new run_id or corrected revision/event")
        tmp = target.with_suffix(".tmp")
        tmp.write_text(encoded, encoding="utf-8")
        os.replace(tmp, target)
        target.chmod(target.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        return {"saved": True, "duplicate": False, "path": str(target), "run": payload}

    def list(self, *, asset: str | None = None) -> list[dict]:
        rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.root.glob("*.json"))]
        if asset is not None:
            rows = [r for r in rows if r.get("asset") == asset]
        return sorted(rows, key=lambda x: x["start_ts"])


class ContextBaselineStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe(value: str) -> str:
        clean = "".join(c if c.isalnum() or c in {"-", "_", "."} else "_" for c in value.strip())
        if not clean:
            raise ValueError("context baseline identifier produced an empty safe filename")
        return clean

    def _path(self, asset: str, name: str, revision: str) -> Path:
        return self.root / f"{self._safe(asset)}__{self._safe(name)}__{self._safe(revision)}.json"

    def save(self, baseline: dict) -> dict:
        target = self._path(baseline["asset"], baseline["name"], baseline["revision"])
        encoded = json.dumps(baseline, indent=2, sort_keys=True)
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing == baseline:
                return {"saved": False, "duplicate": True, "path": str(target), "baseline": baseline}
            raise ValueError("production-context baseline revisions are immutable; create a new revision")
        tmp = target.with_suffix(".tmp")
        tmp.write_text(encoded, encoding="utf-8")
        os.replace(tmp, target)
        target.chmod(target.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        return {"saved": True, "duplicate": False, "path": str(target), "baseline": baseline}

    def get(self, *, asset: str, name: str, revision: str) -> dict:
        path = self._path(asset, name, revision)
        if not path.exists():
            raise FileNotFoundError(f"Production-context baseline {asset}/{name} rev {revision} was not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.root.glob("*.json"))]
