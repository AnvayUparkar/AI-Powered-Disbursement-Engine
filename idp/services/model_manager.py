"""
Model Manager for Offline Model Loading and Storage Management.

Handles local weight directory resolution (e.g. models/ or /mnt/models in Kubernetes pods),
optional S3 model syncing, and offline environment enforcement.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple
from idp.core.config import settings
from idp.core.logging import logger


class ModelManager:
    """
    Manages local and remote model weights for Docling, RapidOCR, and other IDP models.
    Guarantees offline execution without runtime calls to Hugging Face Hub.
    """

    def __init__(
        self,
        model_weights_path: Optional[str] = None,
        model_weights_s3_uri: Optional[str] = None,
        offline_mode: Optional[bool] = None,
        base_dir: Optional[Path] = None,
    ):
        self.raw_path = model_weights_path or settings.MODEL_WEIGHTS_PATH or "models/"
        self.s3_uri = model_weights_s3_uri or settings.MODEL_WEIGHTS_S3_URI
        self.offline_mode = (
            offline_mode if offline_mode is not None else settings.OFFLINE_MODE
        )

        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            # Root directory of the repository (3 levels up from idp/services/model_manager.py)
            self.base_dir = Path(__file__).resolve().parent.parent.parent

        self.models_dir = self._resolve_path(self.raw_path)

        if self.offline_mode:
            self.enforce_offline_environment()

    def _resolve_path(self, path_str: str) -> Path:
        """Resolve absolute path, pod mount path, or workspace-relative path."""
        p = Path(path_str)
        if p.is_absolute():
            return p
        return (self.base_dir / p).resolve()

    def enforce_offline_environment(self) -> None:
        """Set environment flags to strictly prevent runtime Hugging Face Hub downloads."""
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_DATASETS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

    def sync_from_s3_if_needed(self) -> bool:
        """
        If S3 URI is configured and the local models directory is empty or missing,
        sync model weights from the specified S3 bucket.
        """
        if not self.s3_uri:
            return False

        if self.models_dir.exists() and any(self.models_dir.iterdir()):
            logger.info(f"[ModelManager] Local model directory already populated: {self.models_dir}")
            return True

        logger.info(f"[ModelManager] Syncing model weights from {self.s3_uri} to {self.models_dir}...")
        self.models_dir.mkdir(parents=True, exist_ok=True)

        try:
            import boto3
            from urllib.parse import urlparse

            parsed = urlparse(self.s3_uri)
            bucket_name = parsed.netloc
            prefix = parsed.path.lstrip("/")

            s3_client_kwargs = {}
            if settings.AWS_REGION:
                s3_client_kwargs["region_name"] = settings.AWS_REGION
            if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
                s3_client_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
                s3_client_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
            if settings.S3_ENDPOINT_URL:
                s3_client_kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL

            s3 = boto3.client("s3", **s3_client_kwargs)
            paginator = s3.get_paginator("list_objects_v2")

            downloaded_count = 0
            for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    rel_path = key[len(prefix):].lstrip("/") if prefix else key
                    if not rel_path:
                        continue
                    local_file_path = self.models_dir / rel_path
                    local_file_path.parent.mkdir(parents=True, exist_ok=True)
                    logger.debug(f"[ModelManager] Downloading s3://{bucket_name}/{key} -> {local_file_path}")
                    s3.download_file(bucket_name, key, str(local_file_path))
                    downloaded_count += 1

            logger.info(f"[ModelManager] S3 sync completed. Downloaded {downloaded_count} model files.")
            return True
        except Exception as e:
            logger.warning(f"[ModelManager] Failed to sync models from S3 ({self.s3_uri}): {e}")
            return False

    def get_docling_artifacts_path(self) -> Optional[str]:
        """
        Return the local artifacts path for Docling layout & TableFormer models.
        Checks models/docling or root models/ directory.
        """
        docling_sub = self.models_dir / "docling"
        if docling_sub.exists():
            return str(docling_sub)
        if self.models_dir.exists():
            return str(self.models_dir)
        return None

    def get_rapidocr_model_paths(self) -> Dict[str, Optional[str]]:
        """
        Return paths to detection, recognition, and classification ONNX models.
        """
        rapid_dir = self.models_dir / "rapidocr"
        search_dirs = [rapid_dir, self.models_dir]

        det_path = None
        rec_path = None
        cls_path = None

        for d in search_dirs:
            if not d.exists():
                continue

            # Detection model
            for det_name in [
                "ch_PP-OCRv4_det_infer.onnx",
                "ch_PP-OCRv3_det_infer.onnx",
                "PP-OCRv4_det.onnx",
                "det.onnx",
            ]:
                candidate = d / det_name
                if candidate.exists() and not det_path:
                    det_path = str(candidate)
                    break

            # Recognition model
            for rec_name in [
                "ch_PP-OCRv4_rec_infer.onnx",
                "ch_PP-OCRv3_rec_infer.onnx",
                "PP-OCRv4_rec.onnx",
                "rec.onnx",
            ]:
                candidate = d / rec_name
                if candidate.exists() and not rec_path:
                    rec_path = str(candidate)
                    break

            # Classification model
            for cls_name in [
                "ch_ppocr_mobile_v2.0_cls_infer.onnx",
                "cls.onnx",
            ]:
                candidate = d / cls_name
                if candidate.exists() and not cls_path:
                    cls_path = str(candidate)
                    break

        return {
            "det_model_path": det_path,
            "rec_model_path": rec_path,
            "cls_model_path": cls_path,
        }

    def validate_models_present(self) -> Dict[str, bool]:
        """Check presence of required model weight files."""
        rapid = self.get_rapidocr_model_paths()
        docling_path = self.get_docling_artifacts_path()

        has_docling = bool(docling_path and Path(docling_path).exists() and any(Path(docling_path).iterdir()))
        has_rapid = bool(rapid.get("det_model_path") and rapid.get("rec_model_path"))

        return {
            "models_dir_exists": self.models_dir.exists(),
            "docling_models_present": has_docling,
            "rapidocr_models_present": has_rapid,
            "offline_mode_active": self.offline_mode,
        }


# Global singleton instance
model_manager = ModelManager()
