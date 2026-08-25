from __future__ import annotations
import json, os, stat
from pathlib import Path
from pydantic import BaseModel


class JsonRecordStore:
    def __init__(self, root: Path, key_field: str):
        self.root = root; self.key_field = key_field
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, obj: BaseModel) -> dict:
        data = obj.model_dump(mode="json")
        key = str(data[self.key_field])
        target = self.root / f"{key}.json"
        payload = json.dumps(data, sort_keys=True, indent=2)
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if json.loads(existing) != data:
                raise ValueError(f"{self.key_field} {key!r} already exists with different immutable content")
            return {**data, "duplicate": True}
        tmp = target.with_suffix(".tmp"); tmp.write_text(payload, encoding="utf-8"); os.replace(tmp, target)
        target.chmod(target.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        return {**data, "duplicate": False}

    def list(self, asset: str | None = None) -> list[dict]:
        out=[]
        for p in sorted(self.root.glob("*.json")):
            d=json.loads(p.read_text(encoding="utf-8"))
            if asset is None or d.get("asset") == asset: out.append(d)
        return out
