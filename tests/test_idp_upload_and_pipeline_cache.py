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
    assert res_data["status"] in ("UPLOADED", "queued")
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


def test_process_single_document_delegates_to_8001(tmp_path: Path):
    """Verifies that _process_single_document makes HTTP requests to Port 8001 and parses ParsedDocument."""
    from pipeline.nodes.idp_scan import _process_single_document
    import httpx

    doc_file = tmp_path / "pan.pdf"
    doc_file.write_bytes(b"%PDF-1.4 dummy pan")
    doc_id = "DOC_TEST_8001"
    doc_key = "pan"

    parsed_payload = {
        "document_id": doc_id,
        "source": {"filename": "pan.pdf", "mime_type": "application/pdf"},
        "pages": [{"page_number": 1, "width": 595.0, "height": 842.0, "elements": [], "tables": []}],
        "tables": [],
        "elements": [],
        "text": "INCOME TAX DEPARTMENT GOVT OF INDIA\nPAN: ABCDE1234F\nName: Rajesh Sharma",
        "processing": {
            "document_id": doc_id,
            "processing_id": f"proc-{doc_id}",
            "file_type": "pdf",
            "mime_type": "application/pdf",
            "file_size_bytes": 1024,
            "page_count": 1,
            "docling_used": True,
            "ocr_engine": "docling_rapidocr",
            "ocr_model": "PP-OCRv6_MEDIUM",
        },
        "custom_metadata": {
            "llm_extracted_fields": {
                "pan_number": "ABCDE1234F",
                "applicant_name": "Rajesh Sharma",
            }
        },
    }

    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self._json_data = json_data
            self.status_code = status_code

        def json(self):
            return self._json_data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("Error", request=MagicMock(), response=self)

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json=None):
            assert "/api/v1/documents/process" in url
            assert json["document_id"] == doc_id
            return MockResponse({"status": "completed", "result": parsed_payload})

        def get(self, url):
            assert f"/api/v1/documents/{doc_id}" in url
            return MockResponse(parsed_payload)

    with patch("httpx.Client", side_effect=MockClient):
        result = _process_single_document(doc_file, doc_id=doc_id, doc_key=doc_key)
        assert result is not None
        assert result.get("pan_number") == "ABCDE1234F"
        assert result.get("applicant_name") == "Rajesh Sharma"
        assert "INCOME TAX DEPARTMENT" in result.get("_raw_text", "")


def test_process_single_document_returns_none_on_remote_failure(tmp_path: Path):
    """Verifies that Port 8000 NEVER attempts local OCR if Port 8001 is unreachable or errors."""
    from pipeline.nodes.idp_scan import _process_single_document
    import httpx

    doc_file = tmp_path / "kfs.pdf"
    doc_file.write_bytes(b"%PDF-1.4 dummy kfs")
    doc_id = "DOC_TEST_FAIL"
    doc_key = "kfs"

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json=None):
            raise httpx.ConnectError("Connection refused to 8001")

    with patch("httpx.Client", side_effect=FailingClient):
        result = _process_single_document(doc_file, doc_id=doc_id, doc_key=doc_key)
        # MUST return None and NEVER initialize local Docling/RapidOCR
        assert result is None


def test_process_single_document_xml_fast_path(tmp_path: Path):
    """Verifies that XML documents run locally via DocumentSerializer fast-path without remote calls."""
    from pipeline.nodes.idp_scan import _process_single_document

    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<OfflinePaperlessKyc>
  <UidData tkn="token999" uid="xxxxxxxx5678">
    <Poi dob="15-08-1992" gender="M" name="Ramesh Kumar"/>
  </UidData>
</OfflinePaperlessKyc>"""
    xml_file = tmp_path / "aadhaar.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    with patch("httpx.Client") as mock_http:
        result = _process_single_document(xml_file, doc_id="DOC_XML_TEST", doc_key="aadhaar_xml")
        # HTTP client must NOT be called for XML fast path
        mock_http.assert_not_called()
        assert result is not None
        assert result.get("aadhaar_number") == "xxxxxxxx5678"


def test_celery_task_syncs_to_s3_extracted_tier(clean_test_case, tmp_path: Path):
    """Verifies that Celery process_document_task syncs extracted results to S3 Extracted tier for the case."""
    from pipeline.celery_app import process_document_task
    import httpx

    case_id = clean_test_case
    doc_id = f"{case_id}_pan"
    doc_file = S3_RAW_DIR / case_id / "pan.pdf"
    doc_file.parent.mkdir(parents=True, exist_ok=True)
    doc_file.write_bytes(b"%PDF-1.4 dummy pan content")

    parsed_payload = {
        "document_id": doc_id,
        "source": {"filename": "pan.pdf", "mime_type": "application/pdf"},
        "pages": [{"page_number": 1, "width": 595.0, "height": 842.0, "elements": [], "tables": []}],
        "tables": [],
        "elements": [],
        "text": "PAN: ABCDE1234F Name: Sunita Sharma",
        "processing": {
            "document_id": doc_id,
            "processing_id": f"proc-{doc_id}",
            "file_type": "pdf",
            "mime_type": "application/pdf",
            "file_size_bytes": 1024,
            "page_count": 1,
            "docling_used": True,
            "ocr_engine": "docling_rapidocr",
            "ocr_model": "PP-OCRv6_MEDIUM",
        },
        "custom_metadata": {
            "llm_extracted_fields": {
                "pan_number": "ABCDE1234F",
                "applicant_name": "Sunita Sharma",
            }
        },
    }

    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self._json_data = json_data
            self.status_code = status_code

        def json(self):
            return self._json_data

        def raise_for_status(self):
            pass

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json=None):
            return MockResponse({"status": "completed", "result": parsed_payload})

        def get(self, url):
            return MockResponse(parsed_payload)

    with patch("httpx.Client", side_effect=MockClient):
        res = process_document_task.apply(args=[doc_id, str(doc_file), case_id]).get()
        assert res["status"] == "completed"

        # Verify output was persisted to S3 Extracted tier: s3_extracted/{case_id}/pan.json
        s3_extracted_file = S3_EXTRACTED_DIR / case_id / "pan.json"
        assert s3_extracted_file.exists()
        extracted_data = read_json(s3_extracted_file)
        assert extracted_data["pan_number"] == "ABCDE1234F"
        assert extracted_data["applicant_name"] == "Sunita Sharma"


def test_case_upload_only_stages_to_s3_raw_without_idp(clean_test_case):
    """Verifies that uploading to a specific loan case returns UPLOADED and does NOT trigger background Celery/IDP."""
    case_id = clean_test_case
    file_content = b"%PDF-1.4 Minimal test PDF content"
    files = {
        "file": ("kfs_upload.pdf", io.BytesIO(file_content), "application/pdf")
    }
    data = {
        "case_id": case_id,
        "doc_type": "KFS",
    }

    with patch("pipeline.celery_app.process_document_task.delay") as mock_delay:
        response = client.post("/api/v1/documents/upload", files=files, data=data)
        assert response.status_code == 200
        res_data = response.json()
        assert res_data["status"] == "UPLOADED"

        # Verify Celery background IDP task was NOT called
        mock_delay.assert_not_called()

        # Verify raw file exists in S3 raw case directory
        raw_file = S3_RAW_DIR / case_id / "kfs_upload.pdf"
        assert raw_file.exists()


def test_documents_tab_upload_enqueues_idp(clean_test_case):
    """Verifies that uploading to General / Documents tab triggers immediate background IDP."""
    file_content = b"%PDF-1.4 Minimal test PDF content"
    files = {
        "file": ("general_doc.pdf", io.BytesIO(file_content), "application/pdf")
    }
    data = {
        "case_id": "GENERAL",
        "doc_type": "Miscellaneous",
    }

    with patch("pipeline.celery_app.process_document_task.delay") as mock_delay:
        response = client.post("/api/v1/documents/upload", files=files, data=data)
        assert response.status_code == 200
        res_data = response.json()
        assert res_data["status"] == "queued"

        # Verify Celery background IDP task was called
        mock_delay.assert_called_once()

