import io
import os
import shutil
import time
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.document_registry import document_registry
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

    with patch("pipeline.nodes.idp_scan._call_idp_service") as mock_process:
        result_state = idp_scan(initial_state)

        # _call_idp_service must NOT have been called due to cache hit
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

    with patch("pipeline.nodes.idp_scan._call_idp_service", return_value=mock_return) as mock_process:
        result_state = idp_scan(initial_state)

        mock_process.assert_called_once()
        assert "kfs" in result_state["extracted_data"]
        assert result_state["extracted_data"]["kfs"]["kfs_loan_amount"] == 750000.0

        # Verify it persisted to S3 Extracted tier
        extracted_file = S3_EXTRACTED_DIR / case_id / "kfs.json"
        assert extracted_file.exists()
        saved_data = read_json(extracted_file)
        assert saved_data["kfs_loan_amount"] == 750000.0


def test_call_idp_service_delegates_to_8001(tmp_path: Path):
    """Verifies that _call_idp_service makes HTTP requests to Port 8001 and retrieves canonical extraction."""
    from pipeline.nodes.idp_scan import _call_idp_service
    import httpx

    doc_file = tmp_path / "pan.pdf"
    doc_file.write_bytes(b"%PDF-1.4 dummy pan")
    doc_id = "DOC_TEST_8001"
    doc_key = "pan"

    canonical_payload = {
        "pan_number": "ABCDE1234F",
        "applicant_name": "Rajesh Sharma",
        "_raw_text": "INCOME TAX DEPARTMENT GOVT OF INDIA\nPAN: ABCDE1234F\nName: Rajesh Sharma",
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
            return MockResponse({"status": "completed"})

        def get(self, url):
            assert f"/api/v1/documents/{doc_id}/canonical" in url
            return MockResponse(canonical_payload)

    with patch("httpx.Client", side_effect=MockClient):
        result = _call_idp_service(doc_file, doc_id=doc_id, doc_key=doc_key)
        assert result is not None
        assert result.get("pan_number") == "ABCDE1234F"
        assert result.get("applicant_name") == "Rajesh Sharma"
        assert "INCOME TAX DEPARTMENT" in result.get("_raw_text", "")


def test_call_idp_service_returns_none_on_remote_failure(tmp_path: Path):
    """Verifies that Port 8000 NEVER attempts local OCR if Port 8001 is unreachable or errors."""
    from pipeline.nodes.idp_scan import _call_idp_service
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
        result = _call_idp_service(doc_file, doc_id=doc_id, doc_key=doc_key)
        # MUST return None and NEVER initialize local Docling/RapidOCR
        assert result is None


def test_call_idp_service_xml_fast_path(tmp_path: Path):
    """Verifies that XML documents route through IDP service and return canonical extraction."""
    from pipeline.nodes.idp_scan import _call_idp_service
    import httpx

    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<OfflinePaperlessKyc>
  <UidData tkn="token999" uid="xxxxxxxx5678">
    <Poi dob="15-08-1992" gender="M" name="Ramesh Kumar"/>
  </UidData>
</OfflinePaperlessKyc>"""
    xml_file = tmp_path / "aadhaar.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    canonical_xml_payload = {
        "aadhaar_number": "xxxxxxxx5678",
        "aadhaar_xml_present": True,
        "_raw_text": "UidData: xxxxxxxx5678",
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
            assert "/api/v1/documents/process" in url
            return MockResponse({"status": "completed"})

        def get(self, url):
            assert "/canonical" in url
            return MockResponse(canonical_xml_payload)

    with patch("httpx.Client", side_effect=MockClient):
        result = _call_idp_service(xml_file, doc_id="DOC_XML_TEST", doc_key="aadhaar_xml")
        assert result is not None
        assert result.get("aadhaar_number") == "xxxxxxxx5678"
        assert result.get("aadhaar_xml_present") is True


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

    canonical_payload = {
        "pan_number": "ABCDE1234F",
        "applicant_name": "Sunita Sharma",
        "_raw_text": "PAN: ABCDE1234F Name: Sunita Sharma",
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
            if "canonical" in url:
                return MockResponse(canonical_payload)
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


def test_case_document_registry_strictly_shows_s3_raw_files_only(clean_test_case):
    """Verifies that DocumentRegistry.list_all only returns documents physically present in s3_raw for the case."""
    from app.services.document_registry import DocumentRegistry
    case_id = clean_test_case

    # 1. Create 2 real physical files in s3_raw
    raw_dir = S3_RAW_DIR / case_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "Aadhaar Card.pdf").write_bytes(b"%PDF-1.4 sample aadhaar")
    (raw_dir / "Sanction Letter.pdf").write_bytes(b"%PDF-1.4 sample sanction")

    # 2. Create phantom extracted json in s3_extracted that does NOT exist in s3_raw
    ext_dir = S3_EXTRACTED_DIR / case_id
    ext_dir.mkdir(parents=True, exist_ok=True)
    write_json(ext_dir / "kfs.json", {"loan_amount": 500000})

    registry = DocumentRegistry()
    docs = registry.list_all(case_id=case_id)
    doc_names = [d["name"] for d in docs]

    # Only physical files in s3_raw must be returned
    assert "Aadhaar Card.pdf" in doc_names
    assert "Sanction Letter.pdf" in doc_names
    assert "kfs.pdf" not in doc_names
    assert "kfs.json" not in doc_names
    assert len(docs) == 2


def test_deduplication_between_upload_and_disk_scanned_documents():
    """Verifies that merge_and_deduplicate merges PAN Card/PAN and KFS labels without producing duplicates."""
    from app.services.registry.dedup import merge_and_deduplicate

    case_id = "APPL00243685"
    # Dynamic uploads with UI dropdown labels
    dynamic_docs = [
        {
            "id": "doc-upload-1",
            "name": "Mutturaj Pancard.pdf",
            "type": "PAN Card",
            "status": "Pending",
            "caseId": case_id,
            "uploadedTimestamp": 100.0,
        },
        {
            "id": "doc-upload-2",
            "name": "kfs.PDF",
            "type": "Key Fact Statement (KFS)",
            "status": "Pending",
            "caseId": case_id,
            "uploadedTimestamp": 101.0,
        },
    ]

    # Disk-scanned documents with short inferred types
    case_docs = [
        {
            "id": f"doc-{case_id}-mutturaj_pancard",
            "name": "Mutturaj Pancard.pdf",
            "type": "PAN",
            "status": "Completed",
            "caseId": case_id,
            "uploadedTimestamp": 50.0,
        },
        {
            "id": f"doc-{case_id}-kfs",
            "name": "kfs.PDF",
            "type": "KFS",
            "status": "Completed",
            "caseId": case_id,
            "uploadedTimestamp": 51.0,
        },
        {
            "id": f"doc-{case_id}-sanction",
            "name": "sanction_letter_8.PDF",
            "type": "Sanction Letter",
            "status": "Completed",
            "caseId": case_id,
            "uploadedTimestamp": 52.0,
        },
    ]

    merged = merge_and_deduplicate(dynamic_docs, case_docs)

    # Exactly 3 documents must be returned (PAN, KFS, Sanction Letter), with zero duplicates
    assert len(merged) == 3
    names = [d["name"] for d in merged]
    assert names.count("Mutturaj Pancard.pdf") == 1
    assert names.count("kfs.PDF") == 1
    assert names.count("sanction_letter_8.PDF") == 1


def test_upload_lifecycle_statuses_case_vs_general_tab(clean_test_case):
    """Verifies that case upload starts as PENDING, general upload as PROCESSING, and finishes as COMPLETED."""
    from app.services.document_registry import document_registry
    case_id = clean_test_case

    # 1. Case Upload -> PENDING
    file_content = b"%PDF-1.4 Case document"
    files = {"file": ("case_doc.pdf", io.BytesIO(file_content), "application/pdf")}
    data = {"case_id": case_id, "doc_type": "PAN Card"}

    with patch("pipeline.celery_app.process_document_task.delay"):
        resp = client.post("/api/v1/documents/upload", files=files, data=data)
        assert resp.status_code == 200
        doc_id = resp.json()["document_id"]

    doc = document_registry.get_by_id(doc_id)
    assert doc is not None
    assert doc["ocrStatus"] == "PENDING"
    assert doc["extractionStatus"] == "PENDING"
    assert doc["confidence"] == 0.0

    # 2. General Upload -> PROCESSING
    files_gen = {"file": ("general_doc.pdf", io.BytesIO(file_content), "application/pdf")}
    data_gen = {"case_id": "GENERAL", "doc_type": "Miscellaneous"}

    with patch("pipeline.celery_app.process_document_task.delay"):
        resp_gen = client.post("/api/v1/documents/upload", files=files_gen, data=data_gen)
        assert resp_gen.status_code == 200
        doc_gen_id = resp_gen.json()["document_id"]

    doc_gen = document_registry.get_by_id(doc_gen_id)
    assert doc_gen is not None
    assert doc_gen["ocrStatus"] == "PROCESSING"
    assert doc_gen["extractionStatus"] == "PROCESSING"

    # 3. Transition to COMPLETED with extracted result
    extracted_payload = {
        "text": "Extracted PAN ABCDE1234F",
        "extracted_fields": {"pan_number": "ABCDE1234F"},
        "pages": 1,
    }
    document_registry.update_extracted_result(doc_id, extracted_payload)
    doc_completed = document_registry.get_by_id(doc_id)
    assert doc_completed["ocrStatus"] == "COMPLETED"
    assert doc_completed["extractionStatus"] == "COMPLETED"
    assert doc_completed["confidence"] > 90.0


def test_upload_without_case_id_defaults_to_general_and_registers():
    """Uploading without specifying case_id must default case_id to GENERAL in storage and registry."""
    file_content = b"%PDF-1.4 Ad-hoc sandbox document content"
    filename = f"adhoc_test_{uuid.uuid4().hex[:6]}.pdf"
    files = {
        "file": (filename, io.BytesIO(file_content), "application/pdf")
    }

    with patch("pipeline.celery_app.process_document_task.delay") as mock_delay:
        # Omit case_id entirely
        response = client.post("/api/v1/documents/upload", files=files, data={"doc_type": "Miscellaneous"})
        assert response.status_code == 200
        res_data = response.json()
        doc_id = res_data["document_id"]
        assert res_data["status"] == "queued"

        # Verify Celery task received 'GENERAL'
        mock_delay.assert_called_once()
        args = mock_delay.call_args[0]
        assert args[0] == doc_id
        assert "raw-documents/GENERAL/" in args[1]
        assert args[2] == "GENERAL"

        # Verify physical file saved to S3_RAW_DIR / GENERAL
        assert (S3_RAW_DIR / "GENERAL" / filename).exists()

        # Verify registry record caseId is GENERAL
        doc_rec = document_registry.get_by_id(doc_id)
        assert doc_rec is not None
        assert doc_rec["caseId"] == "GENERAL"

        # Verify query with caseId=GENERAL returns this document
        gen_docs = document_registry.list_all(case_id="GENERAL")
        gen_ids = [d["id"] for d in gen_docs]
        assert doc_id in gen_ids





