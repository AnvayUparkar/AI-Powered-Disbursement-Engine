#!/usr/bin/env python
"""Download the Docling ML weights into the repo-local model store.

Fetches the three model families this pipeline actually runs -- the layout
model, TableFormer, and the RapidOCR det/cls/rec ONNX checkpoints -- into
<repo>/models/docling (or $DOCLING_ARTIFACTS_PATH). Once populated, the
pipeline loads every model from that directory and performs no network access
at first use; see idp/services/docling/model_store.py.

Usage:
    python scripts/download_models.py                 # download if missing
    python scripts/download_models.py --force         # re-download everything
    python scripts/download_models.py --check         # report status, exit 1 if incomplete
    python scripts/download_models.py --dest /opt/wt  # explicit destination
"""

import argparse
import logging
import sys
from pathlib import Path

# Allow running as a plain script from anywhere in the repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from idp.services.docling.model_store import (  # noqa: E402
    download_models,
    get_default_artifacts_dir,
    missing_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest", type=Path, default=None,
        help="Destination directory (default: $DOCLING_ARTIFACTS_PATH or <repo>/models/docling)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-download even if files already exist",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Only report whether the weights are present; exit 1 if incomplete",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress progress bars")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    target = args.dest or get_default_artifacts_dir()
    missing = missing_artifacts(target)

    if args.check:
        if missing:
            print(f"INCOMPLETE: {target} is missing: {', '.join(missing)}")
            return 1
        print(f"OK: Docling weights present at {target}")
        return 0

    if not missing and not args.force:
        print(f"OK: Docling weights already present at {target} (use --force to re-download)")
        return 0

    try:
        path = download_models(target, force=args.force, progress=not args.quiet)
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    size_mb = sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / (1024 * 1024)
    print(f"OK: Docling weights downloaded to {path} ({size_mb:.0f} MB)")
    print(
        "Set DOCLING_OFFLINE=true to make the pipeline fail fast rather than "
        "silently falling back to a network download."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
