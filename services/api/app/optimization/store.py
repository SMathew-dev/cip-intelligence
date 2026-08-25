from __future__ import annotations
import json, os, re, stat
from pathlib import Path


def _safe(v: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", v.strip()).strip("-.")


class ImmutableOptimizationStore:
    def __init__(self, root: Path):
        self.root=root; self.root.mkdir(parents=True,exist_ok=True)

    def save(self, kind: str, key: str, payload: dict) -> dict:
        d=self.root/kind; d.mkdir(parents=True,exist_ok=True); p=d/f"{_safe(key)}.json"
        encoded=json.dumps(payload,indent=2,sort_keys=True,default=str)
        if p.exists():
            existing=p.read_text(encoding="utf-8")
            if existing != encoded:
                raise ValueError(f"immutable optimization artifact {key!r} already exists with different content")
            return {"saved":True,"duplicate":True,"path":str(p),**payload}
        tmp=p.with_suffix(".tmp"); tmp.write_text(encoded,encoding="utf-8"); os.replace(tmp,p)
        p.chmod(p.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        return {"saved":True,"duplicate":False,"path":str(p),**payload}

    def get(self, kind: str, key: str) -> dict:
        p=self.root/kind/f"{_safe(key)}.json"
        if not p.exists(): raise FileNotFoundError(key)
        return json.loads(p.read_text(encoding="utf-8"))

    def list(self, kind: str) -> list[dict]:
        d=self.root/kind
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] if d.exists() else []
