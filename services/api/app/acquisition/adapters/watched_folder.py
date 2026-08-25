from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from app.acquisition.adapters.base import ReadOnlyAdapter
from app.acquisition.models import AcquisitionCandidate


class WatchedFolderAdapter(ReadOnlyAdapter):
    """Read stable files from a plant/network export folder.

    Files younger than settle_seconds are ignored to avoid reading exports while
    another system is still writing them. Temporary/hidden files are ignored.
    """

    def discover(self) -> list[AcquisitionCandidate]:
        folder = Path(self.source.config["folder"]).expanduser().resolve()
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(f"Watched folder does not exist: {folder}")

        patterns = self.source.config.get("patterns", ["*.csv"])
        settle_seconds = int(self.source.config.get("settle_seconds", 10))
        now = datetime.now(timezone.utc).timestamp()
        candidates: dict[str, AcquisitionCandidate] = {}

        for pattern in patterns:
            for path in folder.glob(pattern):
                if not path.is_file():
                    continue
                if path.name.startswith(".") or path.suffix.lower() in {".tmp", ".part", ".partial", ".crdownload"}:
                    continue
                st = path.stat()
                age = max(0.0, now - st.st_mtime)
                if age < settle_seconds:
                    continue
                resolved = str(path.resolve())
                candidates[resolved] = AcquisitionCandidate(
                    source_name=self.source.name,
                    source_ref=resolved,
                    filename=path.name,
                    metadata={
                        "size_bytes": st.st_size,
                        "mtime_ns": st.st_mtime_ns,
                        "inode": getattr(st, "st_ino", None),
                        "settled_seconds": round(age, 3),
                    },
                )
        return sorted(candidates.values(), key=lambda c: (c.metadata.get("mtime_ns", 0), c.source_ref))

    def read(self, candidate: AcquisitionCandidate) -> bytes:
        path = Path(candidate.source_ref)
        # O_RDONLY makes the adapter's intent explicit at OS level. The Python
        # API never opens the source with write/append flags.
        fd = os.open(path, os.O_RDONLY)
        try:
            with os.fdopen(fd, "rb", closefd=False) as handle:
                return handle.read()
        finally:
            os.close(fd)
