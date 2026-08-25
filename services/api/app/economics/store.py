from __future__ import annotations

import json
import os
import stat
from pathlib import Path


class ImmutableJsonStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str, name: str, revision: str) -> Path:
        return self.root / kind / name / f"{revision}.json"

    def save(self, kind: str, name: str, revision: str, payload: dict) -> dict:
        path = self._path(kind, name, revision)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, indent=2, sort_keys=True, default=str)
        if path.exists():
            current = path.read_text(encoding="utf-8")
            if current != encoded:
                raise ValueError(f"{kind} {name!r} revision {revision!r} is immutable; create a new revision")
            return {"duplicate": True, "artifact_path": str(path), "result": payload}
        path.write_text(encoded, encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return {"duplicate": False, "artifact_path": str(path), "result": payload}

    def get(self, kind: str, name: str, revision: str) -> dict:
        path = self._path(kind, name, revision)
        if not path.exists():
            raise FileNotFoundError(f"{kind} {name!r} revision {revision!r} was not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self, kind: str) -> list[dict]:
        base = self.root / kind
        if not base.exists():
            return []
        out = []
        for path in sorted(base.glob("*/*.json")):
            out.append(json.loads(path.read_text(encoding="utf-8")))
        return out
