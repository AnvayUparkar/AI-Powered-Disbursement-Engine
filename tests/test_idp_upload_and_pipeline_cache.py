import io
import os
import shutil
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from config import S3_RAW_DIR, S3_EXTRACTED_DIR
from pipeline.nodes.idp_scan import idp_scan
from pipeline.state import PipelineState
from pipeline.storage import write_json, read_json

client = TestClient(app)


@pytest.fixture
def clean_test_case():
    case_id = "TEST_CACHE_CASE_999"
    raw_dir = S3_RAW_DIR / case_id
    extracted_dir = S3_EXTRACTED_DIR / case_id

    # Cleanup before
    shutil.rmtree(raw_dir, ignore_errors=True)
    shutil.rmtree(extracted_dir, ignore_errors=True)

    yield case_id

    # Cleanup after
    shutil.rmtree(raw_dir, ignore_errors=True)
    shutil.rmtree(extracted_dir, ignore_errors=True)


def test_upload_persists_to_s3_raw_and_registers(clean_test_case):
    """Verifies that uploading a document persists raw PDF to s3_raw instantaneously and registers it."""
    case_id = clean_test_case
    file_content = b"%PDF-1.4 Minimal test PDF content"
    files = {
        "file": ("sanction_letter_1.pdf", io.BytesIO(file_content), "application/pdf")
    }
    data = {
        "case_id": case_id,
        "doc_type": "Sanction Letter",
    }

    response = client.post("/api/v1/documents/upload", files=files, data=data)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "UPLOADED"
    assert res_data["processing_time_seconds"] < 1.0

    # 1. Check raw file in s3_raw
    raw_file = S3_RAW_DIR / case_id / "sanction_letter_1.pdf"
    assert raw_file.exists()
    assert raw_file.read_bytes() == file_content


def test_idp_scan_cache_hit_skips_ocr(clean_test_case):
    """Verifies that idp_scan reuses pre-extracted JSON and skips duplicate OCR."""
    case_id = clean_test_case
    raw_dir = S3_RAW_DIR / case_id
    extracted_dir = S3_EXTRACTED_DIR / case_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir.mkdir(parents=True, exist_ok=True)

    raw_file = raw_dir / "sanction_letter.pdf"
    raw_file.write_bytes(b"%PDF-1.4 dummy")

    # Ensure extracted file mtime is >= raw_file mtime
    extracted_file = extracted_dir / "sanction_letter.json"
    cached_payload = {
        "loan_amount": 500000.0,
        "interest_rate": 10.5,
        "_raw_text": "Sanctioned Loan Amount: INR 5,00,000 at 10.5% p.a.",
        "_components": {"tables": []},
    }
    write_json(extracted_file, cached_payload)
    os.utime(extracted_file, (time.time() + 10, time.time() + 10))

    initial_state: PipelineState = {
        "loan_id": case_id,
        "raw_doc_paths": {"sanction_letter.pdf": str(raw_file)},
        "extracted_data": {},
        "errors": [],
        "node_history": [],
    }

    with patch("pipeline.nodes.idp_scan._process_single_document") as mock_process:
        result_state = idp_scan(initial_state)

        # _process_single_document must NOT have been called due to cache hit
        mock_process.assert_not_called()

        assert "sanction_letter" in result_state["extracted_data"]
        assert result_state["extracted_data"]["sanction_letter"]["loan_amount"] == 500000.0
        assert "idp_scan" in result_state["node_history"]


def test_idp_scan_runs_ocr_on_cache_miss_or_stale(clean_test_case):
    """Verifies that idp_scan invokes OCR when cache is missing or stale, then saves extraction."""
    case_id = clean_test_case
    raw_dir = S3_RAW_DIR / case_id
    raw_dir.mkdir(parents=True, exist_ok=True)

    raw_file = raw_dir / "kfs.pdf"
    raw_file.write_bytes(b"%PDF-1.4 fresh file")

    initial_state: PipelineState = {
        "loan_id": case_id,
        "raw_doc_paths": {"kfs.pdf": str(raw_file)},
        "extracted_data": {},
        "errors": [],
        "node_history": [],
    }

    mock_return = {
        "kfs_loan_amount": 750000.0,
        "_raw_text": "Key Fact Statement Loan Amount 750000",
        "_components": {},
    }

    with patch("pipeline.nodes.idp_scan._process_single_document", return_value=mock_return) as mock_process:
        result_state = idp_scan(initial_state)

        mock_process.assert_called_once()
        assert "kfs" in result_state["extracted_data"]
        assert result_state["extracted_data"]["kfs"]["kfs_loan_amount"] == 750000.0

        # Verify it persisted to S3 Extracted tier
        extracted_file = S3_EXTRACTED_DIR / case_id / "kfs.json"
        assert extracted_file.exists()
        saved_data = read_json(extracted_file)
        assert saved_data["kfs_loan_amount"] == 750000.0
