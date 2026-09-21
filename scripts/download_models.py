#!/usr/bin/env python3
"""Pre-fetch every model weight the IDP service needs so pods run with zero outbound network.

Run at image build time (see docker/Dockerfile.backend*):

    python scripts/download_models.py --dest /opt/models/docling

and point Docling at the result with DOCLING_ARTIFACTS_PATH=/opt/models/docling (a native Docling
setting) plus HF_HUB_OFFLINE=1. With an artifacts path set, Docling refuses to download anything and
fails loudly if a weight is missing, so a wrong/incomplete bake shows up as an error, not a silent fetch.

Only what the pipeline actually enables is fetched: layout model, TableFormer, and RapidOCR.
Optional enrichment models (code/formula, picture classifier, VLMs) are off in PdfPipelineOptions here.
"""
import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

# RapidOCR recognizers to bake in, as '<backend>:<lang>' (docling's spec). idp uses the onnxruntime backend.
DEFAULT_RAPIDOCR_MODELS = ["onnxruntime:en"]

# Sub-folders docling creates under --dest; each must exist and be non-empty after a successful bake.
REQUIRED_SUBDIRS = ["RapidOcr"]


def verify_artifacts(dest: Path) -> List[str]:
    """Return a list of problems; empty means the bake looks complete."""
    problems: List[str] = []
    if not dest.is_dir():
        return [f"{dest} does not exist"]
    entries = [p for p in dest.iterdir() if not p.name.startswith(".")]
    if len(entries) < 3:
        problems.append(f"expected layout, TableFormer and RapidOCR folders under {dest}, found {len(entries)} entries")
    for name in REQUIRED_SUBDIRS:
        sub = dest / name
        if not sub.is_dir() or not any(sub.rglob("*.onnx")):
            problems.append(f"{sub} is missing or has no .onnx weights")
    weight_files = [p for p in dest.rglob("*") if p.is_file() and p.suffix in {".onnx", ".safetensors", ".pt", ".bin"}]
    if not weight_files:
        problems.append(f"no model weight files found anywhere under {dest}")
    return problems


def download(dest: Path, rapidocr_models: Optional[List[str]] = None, attempts: int = 4, backoff: float = 10.0) -> Path:
    """Fetch the weights, retrying transient network failures (DNS/TLS blips are common in image builds).

    Already-fetched files are kept between attempts, so a retry only re-requests what is missing.
    """
    from docling.utils.model_downloader import download_models

    dest.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, attempts + 1):
        try:
            return download_models(
                output_dir=dest,
                with_layout=True,
                with_tableformer=True,
                with_code_formula=False,
                with_picture_classifier=False,
                with_rapidocr=True,
                rapidocr_models=rapidocr_models or DEFAULT_RAPIDOCR_MODELS,
                progress=False,
            )
        except Exception as exc:  # noqa: BLE001 - any failure here is a network/hub error worth retrying
            if attempt == attempts:
                raise
            print(f"download attempt {attempt}/{attempts} failed ({type(exc).__name__}: {exc}); retrying in {backoff:.0f}s",
                  file=sys.stderr)
            time.sleep(backoff)
    raise AssertionError("unreachable")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", type=Path, required=True, help="Directory to write model artifacts into")
    parser.add_argument(
        "--rapidocr-models",
        default=",".join(DEFAULT_RAPIDOCR_MODELS),
        help="Comma-separated '<backend>:<lang>' RapidOCR specs (default: %(default)s)",
    )
    parser.add_argument("--verify-only", action="store_true", help="Skip downloading; only check --dest is complete")
    args = parser.parse_args(argv)

    if not args.verify_only:
        specs = [s.strip() for s in args.rapidocr_models.split(",") if s.strip()]
        download(args.dest, specs)

    problems = verify_artifacts(args.dest)
    if problems:
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        return 1
    print(f"OK: model artifacts complete under {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
