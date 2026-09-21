"""
Unit and integration tests for ModelManager and offline model loading.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from idp.services.model_manager import ModelManager


class TestModelManager:
    """Test suite for offline model path resolution and management."""

    def test_offline_environment_variables_enforced(self, tmp_path: Path):
        """Verify offline environment variables are strictly set."""
        manager = ModelManager(
            model_weights_path=str(tmp_path),
            offline_mode=True,
            base_dir=tmp_path,
        )
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"
        assert os.environ.get("HF_DATASETS_OFFLINE") == "1"

    def test_path_resolution_relative_and_absolute(self, tmp_path: Path):
        """Verify relative paths resolve relative to base_dir, and absolute paths are preserved."""
        # Relative path
        rel_manager = ModelManager(
            model_weights_path="models",
            base_dir=tmp_path,
        )
        assert rel_manager.models_dir == (tmp_path / "models").resolve()

        # Absolute path (e.g. Kubernetes pod mount /mnt/models or isolated tmp_path)
        abs_target = (tmp_path / "pod_mount" / "models").resolve()
        abs_manager = ModelManager(
            model_weights_path=str(abs_target),
            base_dir=tmp_path,
        )
        assert abs_manager.models_dir == abs_target

    def test_docling_artifacts_path_resolution(self, tmp_path: Path):
        """Verify docling artifacts path is discovered in models/docling or root models/."""
        models_dir = tmp_path / "models"
        docling_dir = models_dir / "docling"
        docling_dir.mkdir(parents=True, exist_ok=True)
        (docling_dir / "config.json").write_text("{}", encoding="utf-8")

        manager = ModelManager(
            model_weights_path=str(models_dir),
            base_dir=tmp_path,
        )
        artifacts_path = manager.get_docling_artifacts_path()
        assert artifacts_path is not None
        assert Path(artifacts_path) == docling_dir

    def test_empty_docling_subfolder_returns_none(self, tmp_path: Path):
        """Verify docling artifacts path returns None if docling/ only has empty subdirectories."""
        models_dir = tmp_path / "models"
        docling_dir = models_dir / "docling"
        empty_sub = docling_dir / "docling-project--docling-layout-heron"
        empty_sub.mkdir(parents=True, exist_ok=True)

        manager = ModelManager(
            model_weights_path=str(models_dir),
            base_dir=tmp_path,
        )
        assert manager.get_docling_artifacts_path() is None
        validation = manager.validate_models_present()
        assert validation["docling_models_present"] is False

    def test_rapidocr_model_paths_discovery(self, tmp_path: Path):
        """Verify RapidOCR ONNX model files are detected in rapidocr/ subfolder."""
        models_dir = tmp_path / "models"
        rapid_dir = models_dir / "rapidocr"
        rapid_dir.mkdir(parents=True, exist_ok=True)

        det_file = rapid_dir / "ch_PP-OCRv4_det_infer.onnx"
        rec_file = rapid_dir / "ch_PP-OCRv4_rec_infer.onnx"
        cls_file = rapid_dir / "ch_ppocr_mobile_v2.0_cls_infer.onnx"

        det_file.write_bytes(b"dummy_det_weights")
        rec_file.write_bytes(b"dummy_rec_weights")
        cls_file.write_bytes(b"dummy_cls_weights")

        manager = ModelManager(
            model_weights_path=str(models_dir),
            base_dir=tmp_path,
        )

        paths = manager.get_rapidocr_model_paths()
        assert paths["det_model_path"] == str(det_file)
        assert paths["rec_model_path"] == str(rec_file)
        assert paths["cls_model_path"] == str(cls_file)

        validation = manager.validate_models_present()
        assert validation["models_dir_exists"] is True
        assert validation["rapidocr_models_present"] is True

    def test_empty_models_dir_graceful_handling(self, tmp_path: Path):
        """Verify empty models directory does not crash and reports models absent."""
        empty_dir = tmp_path / "empty_models"
        manager = ModelManager(
            model_weights_path=str(empty_dir),
            base_dir=tmp_path,
        )
        assert manager.get_rapidocr_model_paths()["det_model_path"] is None
        validation = manager.validate_models_present()
        assert validation["models_dir_exists"] is False
        assert validation["rapidocr_models_present"] is False
        assert validation["docling_models_present"] is False

    @patch("boto3.client")
    def test_s3_sync_mock(self, mock_boto_client, tmp_path: Path):
        """Verify S3 model sync downloads objects to the local models directory."""
        models_dir = tmp_path / "models_s3"
        manager = ModelManager(
            model_weights_path=str(models_dir),
            model_weights_s3_uri="s3://disbursement-documents/models/",
            base_dir=tmp_path,
        )

        # Mock S3 paginator response
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "models/rapidocr/ch_PP-OCRv4_det_infer.onnx"},
                    {"Key": "models/docling/config.json"},
                ]
            }
        ]

        def fake_download(bucket, key, dest):
            p = Path(dest)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("model_data", encoding="utf-8")

        mock_s3.download_file.side_effect = fake_download

        res = manager.sync_from_s3_if_needed()
        assert res is True
        assert (models_dir / "rapidocr" / "ch_PP-OCRv4_det_infer.onnx").exists()
        assert (models_dir / "docling" / "config.json").exists()

    @pytest.mark.asyncio
    async def test_startup_lifespan_prewarms_converters(self, tmp_path: Path):
        """Verify that starting IDP app prewarms converters during lifespan."""
        from idp.main import lifespan
        from fastapi import FastAPI

        with patch("idp.services.docling.pipeline.prewarm_docling_converters") as mock_prewarm:
            dummy_app = FastAPI()
            async with lifespan(dummy_app):
                pass
            mock_prewarm.assert_called_once()

