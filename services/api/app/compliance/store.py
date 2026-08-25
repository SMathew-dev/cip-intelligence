from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .models import ValidatedRecipe


class RecipeStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe(value: str) -> str:
        clean = "".join(c if c.isalnum() or c in {"-", "_", "."} else "_" for c in value.strip())
        if not clean:
            raise ValueError("recipe name/revision produced an empty safe filename")
        return clean

    def _path(self, recipe: ValidatedRecipe) -> Path:
        return self.root / f"{self._safe(recipe.asset)}__{self._safe(recipe.name)}__{self._safe(recipe.revision)}.json"

    def list(self) -> list[ValidatedRecipe]:
        recipes: list[ValidatedRecipe] = []
        for path in sorted(self.root.glob("*.json")):
            recipes.append(ValidatedRecipe.model_validate_json(path.read_text(encoding="utf-8")))
        return recipes

    def save(self, recipe: ValidatedRecipe) -> dict:
        target = self._path(recipe)
        payload = recipe.model_dump(mode="json")
        serialized = json.dumps(payload, indent=2, sort_keys=True)
        if target.exists():
            existing = ValidatedRecipe.model_validate_json(target.read_text(encoding="utf-8"))
            if existing.model_dump(mode="json") == payload:
                return {"saved": False, "duplicate": True, "path": str(target), "recipe": payload}
            raise ValueError(
                "approved recipe revisions are immutable; create a new revision instead of changing an existing revision"
            )

        tmp = target.with_suffix(".tmp")
        tmp.write_text(serialized, encoding="utf-8")
        os.replace(tmp, target)
        return {"saved": True, "duplicate": False, "path": str(target), "recipe": payload}

    def select_effective(self, asset: str, at: datetime, *, name: str | None = None) -> ValidatedRecipe:
        if at.tzinfo is None:
            raise ValueError("cycle timestamp must be timezone-aware")
        candidates = []
        for recipe in self.list():
            if recipe.asset != asset:
                continue
            if name is not None and recipe.name != name:
                continue
            if recipe.effective_from <= at and (recipe.effective_to is None or at < recipe.effective_to):
                candidates.append(recipe)
        if not candidates:
            raise FileNotFoundError(f"No effective validated recipe found for asset {asset!r} at {at.isoformat()}.")

        logical_names = {r.name for r in candidates}
        if name is None and len(logical_names) > 1:
            refs = sorted(f"{r.name} rev {r.revision}" for r in candidates)
            raise ValueError(
                f"Multiple validated recipes are effective for {asset}; the cycle must identify which recipe ran: {refs}"
            )

        # A later revision of the same logical recipe supersedes earlier revisions
        # from its effective_from timestamp, even if the older record was left
        # open-ended. This lets approved revisions remain immutable.
        latest_start = max(r.effective_from for r in candidates)
        latest = [r for r in candidates if r.effective_from == latest_start]
        if len(latest) != 1:
            refs = [f"{r.name} rev {r.revision}" for r in latest]
            raise ValueError(f"Ambiguous effective recipe revision for {asset}: {refs}")
        return latest[0]
