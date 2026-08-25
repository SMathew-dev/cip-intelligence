from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.acquisition.service import AcquisitionService


def main() -> None:
    parser = argparse.ArgumentParser(description="CIP Intelligence read-only acquisition worker")
    parser.add_argument("--source", required=True, help="Saved acquisition source name")
    parser.add_argument("--once", action="store_true", help="Run one discovery/ingestion pass and exit")
    parser.add_argument("--runtime", default=None, help="Override runtime root")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[4]
    runtime = Path(args.runtime).resolve() if args.runtime else repo_root / "runtime"
    service = AcquisitionService(runtime)
    source = service.source_store.load(args.source)

    while True:
        result = service.run_source(args.source)
        print(json.dumps(result, indent=2, default=str), flush=True)
        if args.once:
            break
        time.sleep(source.poll_seconds)


if __name__ == "__main__":
    main()
