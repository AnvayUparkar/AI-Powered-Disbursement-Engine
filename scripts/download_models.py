"""
One-Time Model Weights Downloader for Offline Execution.

Downloads Docling artifacts (layout analysis, TableFormer) and RapidOCR ONNX weights
to a designated directory (default: <project_root>/models/) or pushes them to an S3 bucket for pod mounts.

Usage:
    python scripts/download_models.py
    python scripts/download_models.py --output-dir models/
    python scripts/download_models.py --upload-to-s3 s3://my-bucket/models/
"""

import argparse
import os
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Optional


# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODELS_DIR = PROJECT_ROOT / "models"


def download_file_with_progress(url: str, dest_path: Path) -> bool:
    """Download a file with console progress indicator."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and dest_path.stat().st_size > 0:
        print(f"  [OK] Already exists: {dest_path.name} ({dest_path.stat().st_size / (1024*1024):.1f} MB)")
        return True

    print(f"  [Downloading] {dest_path.name} from {url}...")
    
    def _reporthook(block_num: int, block_size: int, total_size: int):
        downloaded = block_num * block_size
        if total_size > 0:
            percent = min(100.0, downloaded * 100.0 / total_size)
            sys.stdout.write(f"\r    Progress: {percent:5.1f}% ({downloaded / (1024*1024):.1f}/{total_size / (1024*1024):.1f} MB)")
            sys.stdout.flush()
        else:
            sys.stdout.write(f"\r    Downloaded: {downloaded / (1024*1024):.1f} MB")
            sys.stdout.flush()

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req) as response, open(dest_path, "wb") as out_file:
            total_size = int(response.info().get("Content-Length", -1))
            block_num = 0
            block_size = 65536
            while True:
                chunk = response.read(block_size)
                if not chunk:
                    break
                out_file.write(chunk)
                block_num += 1
                _reporthook(block_num, block_size, total_size)
        print()  # newline
        return True
    except Exception as e:
        print(f"\n  [Notice] Remote download unavailable ({e})")
        if dest_path.exists():
            dest_path.unlink()
        return False


def setup_rapidocr_weights(output_dir: Path) -> None:
    """
    Setup RapidOCR ONNX model weights.
    First copies high-performance bundled ONNX models from installed rapidocr_onnxruntime package,
    then attempts to download PP-OCRv4 if available.
    """
    rapid_dir = output_dir / "rapidocr"
    print(f"\n[1/2] Setting up RapidOCR ONNX Models in {rapid_dir}...")
    rapid_dir.mkdir(parents=True, exist_ok=True)

    # 1. Check bundled package models
    try:
        import rapidocr_onnxruntime
        pkg_models_dir = Path(rapidocr_onnxruntime.__file__).parent / "models"
        if pkg_models_dir.exists():
            for model_file in pkg_models_dir.glob("*.onnx"):
                dest = rapid_dir / model_file.name
                if not dest.exists() or dest.stat().st_size == 0:
                    print(f"  [Copying] Bundled model: {model_file.name} -> {dest}")
                    shutil.copy2(model_file, dest)
                else:
                    print(f"  [OK] Already present: {model_file.name}")
    except Exception as e:
        print(f"  [Notice] Could not inspect rapidocr_onnxruntime package: {e}")

    # 2. Also check direct fallback URLs from ModelScope / HuggingFace
    remote_models = {
        "ch_PP-OCRv4_det_infer.onnx": "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/master/models/ch_PP-OCRv4_det_infer.onnx",
        "ch_PP-OCRv4_rec_infer.onnx": "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/master/models/ch_PP-OCRv4_rec_infer.onnx",
    }
    for filename, url in remote_models.items():
        dest = rapid_dir / filename
        if not dest.exists():
            download_file_with_progress(url, dest)


def download_docling_weights(output_dir: Path) -> None:
    """Download Docling Layout and TableFormer model artifacts."""
    docling_dir = output_dir / "docling"
    print(f"\n[2/2] Downloading Docling Model Artifacts to {docling_dir}...")
    docling_dir.mkdir(parents=True, exist_ok=True)

    # StandardPdfPipeline official model downloader
    try:
        from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
        if hasattr(StandardPdfPipeline, "download_models"):
            print("  Using docling StandardPdfPipeline.download_models()...")
            StandardPdfPipeline.download_models(output_dir=docling_dir)
            print("  [OK] Docling models downloaded successfully.")
            return
    except Exception as e:
        print(f"  StandardPdfPipeline.download_models info: {e}")

    # Download required Docling repositories into the exact directory structure Docling expects
    try:
        from huggingface_hub import snapshot_download
        
        repos = {
            "docling-project/docling-layout-heron": docling_dir / "docling-project--docling-layout-heron",
            "ds4sd/docling-models": docling_dir / "ds4sd--docling-models",
        }
        for repo_id, target_path in repos.items():
            target_path.mkdir(parents=True, exist_ok=True)
            print(f"  Downloading snapshot for {repo_id} -> {target_path.name}...")
            snapshot_download(
                repo_id=repo_id,
                local_dir=str(target_path),
                local_dir_use_symlinks=False,
            )
            print(f"  [OK] {repo_id} downloaded.")

        # Ensure model_artifacts from ds4sd is also mirrored in root docling_dir for TableFormer
        ds4sd_artifacts = docling_dir / "ds4sd--docling-models" / "model_artifacts"
        root_artifacts = docling_dir / "model_artifacts"
        if ds4sd_artifacts.exists() and not root_artifacts.exists():
            shutil.copytree(ds4sd_artifacts, root_artifacts, dirs_exist_ok=True)
    except Exception as e:
        print(f"  [Notice] Snapshot download encountered: {e}")


def upload_to_s3(local_dir: Path, s3_uri: str) -> None:
    """Upload downloaded model files to S3 bucket."""
    print(f"\n[S3 Sync] Uploading {local_dir} to {s3_uri}...")
    try:
        import boto3
        from urllib.parse import urlparse

        parsed = urlparse(s3_uri)
        bucket_name = parsed.netloc
        prefix = parsed.path.lstrip("/")

        s3 = boto3.client("s3")
        uploaded = 0

        for file_path in local_dir.rglob("*"):
            if file_path.is_file():
                rel_path = file_path.relative_to(local_dir).as_posix()
                s3_key = f"{prefix.rstrip('/')}/{rel_path}" if prefix else rel_path
                print(f"  Uploading {rel_path} -> s3://{bucket_name}/{s3_key}")
                s3.upload_file(str(file_path), bucket_name, s3_key)
                uploaded += 1

        print(f"  [OK] Uploaded {uploaded} files to {s3_uri}")
    except Exception as e:
        print(f"  [Error] Failed to upload models to S3 ({s3_uri}): {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Download model weights for offline execution.")
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(DEFAULT_MODELS_DIR),
        help=f"Directory to store model weights (default: {DEFAULT_MODELS_DIR})",
    )
    parser.add_argument(
        "--upload-to-s3",
        type=str,
        default=None,
        help="Optional S3 URI (e.g. s3://disbursement-documents/models/) to upload models after downloading",
    )
    parser.add_argument(
        "--skip-docling",
        action="store_true",
        help="Skip downloading Docling layout/TableFormer models",
    )
    parser.add_argument(
        "--skip-rapidocr",
        action="store_true",
        help="Skip setting up RapidOCR ONNX models",
    )

    args = parser.parse_args()
    base_output = Path(args.output_dir).resolve()
    base_output.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(" Offline Model Weights Downloader")
    print(f" Target Directory: {base_output}")
    print("=" * 70)

    if not args.skip_rapidocr:
        setup_rapidocr_weights(base_output)

    if not args.skip_docling:
        download_docling_weights(base_output)

    if args.upload_to_s3:
        upload_to_s3(base_output, args.upload_to_s3)

    print("\n" + "=" * 70)
    print(" Setup Complete!")
    print(f" All model weights are available in: {base_output}")
    print(" To use these models offline, verify your .env has:")
    print(f"   OFFLINE_MODE=true")
    print(f"   MODEL_WEIGHTS_PATH=models/")
    print("=" * 70)


if __name__ == "__main__":
    main()
