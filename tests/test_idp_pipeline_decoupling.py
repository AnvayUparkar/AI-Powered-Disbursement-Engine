"""Tests verifying full HTTP boundary decoupling between pipeline and IDP service.

Validates:
1. Zero idp/ imports in pipeline/nodes/idp_scan.py and pipeline/celery_app.py
2. HTTP client delegation and error handling in _call_idp_service
3. S3 extraction cache hit and miss behavior in idp_scan
4. Sidecar ingestion preserving face embeddings, DMS status, and OTP audit
5. Canonical extraction output contract compliance in canonical_builder
"""
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config import IDP_SERVICE_URL
from idp.models.document import DocumentSource, PageInformation, ParsedDocument
from idp.models.processing import ProcessingMetadata
from idp.services.output.canonical_builder import build_canonical_extracted_dict
from pipeline.nodes.idp_scan import _call_idp_service, idp_scan
from pipeline.state import PipelineState


def test_idp_scan_no_idp_imports():
    """Static analysis: ensures pipeline/nodes/idp_scan.py contains zero direct IDP dependencies."""
    root_dir = Path(__file__).resolve().parent.parent
    scan_path = root_dir / "pipeline" / "nodes" / "idp_scan.py"
    content = scan_path.read_text(encoding="utf-8")

    forbidden = [
        "from idp",
        "import idp",
        "ParsedDocument",
        "DocumentSerializer",
        "FieldLocationResolver",
    ]
    for pattern in forbidden:
        assert pattern not in content, f"Found forbidden import/symbol '{pattern}' in idp_scan.py"


def test_llm_structure_no_idp_imports():
    """Static analysis: ensures pipeline/nodes/llm_structure.py contains zero direct IDP dependencies."""
    root_dir = Path(__file__).resolve().parent.parent
    node_path = root_dir / "pipeline" / "nodes" / "llm_structure.py"
    content = node_path.read_text(encoding="utf-8")

    forbidden = [
        "from idp",
        "import idp",
        "FieldLocationResolver",
    ]
    for pattern in forbidden:
        assert pattern not in content, f"Found forbidden import/symbol '{pattern}' in llm_structure.py"


def test_idp_documents_no_pipeline_or_app_imports():
    """Static analysis: ensures idp/api/routes/documents.py contains zero imports from pipeline or app."""
    root_dir = Path(__file__).resolve().parent.parent
    doc_route_path = root_dir / "idp" / "api" / "routes" / "documents.py"
    content = doc_route_path.read_text(encoding="utf-8")

    forbidden = [
        "from pipeline",
        "import pipeline",
        "from app",
        "import app",
        "process_document_task",
        "document_registry",
        "SINGLETON_CANONICAL_TYPES",
        "S3_RAW_DIR",
    ]
    for pattern in forbidden:
        assert pattern not in content, f"Found forbidden import/symbol '{pattern}' in idp/api/routes/documents.py"


def test_app_main_no_idp_routes_import():
    """Static analysis: ensures app/main.py does not mount idp.api.routes.documents directly."""
    root_dir = Path(__file__).resolve().parent.parent
    main_path = root_dir / "app" / "main.py"
    content = main_path.read_text(encoding="utf-8")

    assert "idp_documents" not in content
    assert "from idp.api.routes" not in content


def test_global_zero_cross_imports_between_idp_and_pipeline():
    """Static analysis: enforces zero cross-service imports between idp/ and pipeline/."""
    root_dir = Path(__file__).resolve().parent.parent

    # 1. idp/ must never import pipeline
    idp_dir = root_dir / "idp"
    for py_file in idp_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert not (stripped.startswith("from pipeline") or stripped.startswith("import pipeline")), (
                f"Forbidden pipeline import in {py_file.relative_to(root_dir)}: {line}"
            )

    # 2. pipeline/ must never import idp
    pipeline_dir = root_dir / "pipeline"
    for py_file in pipeline_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert not (stripped.startswith("from idp") or stripped.startswith("import idp")), (
                f"Forbidden idp import in {py_file.relative_to(root_dir)}: {line}"
            )



def test_call_idp_service_posts_then_gets_canonical(monkeypatch: pytest.MonkeyPatch):
    """Unit: _call_idp_service triggers /process POST and returns canonical GET JSON."""
    monkeypatch.setattr("pipeline.nodes.idp_scan.USE_REMOTE_IDP", True)

    canonical_data = {
        "_raw_text": "test document text",
        "aadhaar_xml_present": True,
        "name": "Jane Doe",
    }

    mock_client = MagicMock()
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post_resp.raise_for_status = MagicMock()

    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.raise_for_status = MagicMock()
    mock_get_resp.json.return_value = canonical_data

    mock_client.post.return_value = mock_post_resp
    mock_client.get.return_value = mock_get_resp

    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value = mock_client

        file_p = Path("dummy_pan.pdf")
        res = _call_idp_service(file_p, doc_id="DOC-001", doc_key="pan")

        assert res == canonical_data
        mock_client.post.assert_called_once_with(
            f"{IDP_SERVICE_URL}/api/v1/documents/process",
            json={"document_id": "DOC-001", "s3_key": str(file_p)},
        )
        mock_client.get.assert_called_once_with(
            f"{IDP_SERVICE_URL}/api/v1/documents/DOC-001/canonical",
        )


def test_call_idp_service_use_remote_idp_disabled(monkeypatch: pytest.MonkeyPatch):
    """Unit: when USE_REMOTE_IDP is False, _call_idp_service skips HTTP calls and returns None."""
    monkeypatch.setattr("pipeline.nodes.idp_scan.USE_REMOTE_IDP", False)

    with patch("httpx.Client") as mock_client_cls:
        res = _call_idp_service(Path("some/file.pdf"), doc_id="DOC-001", doc_key="pan")
        assert res is None
        mock_client_cls.assert_not_called()


def test_idp_scan_cache_hit_skips_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Unit: valid cached JSON in S3 extracted tier newer than raw source bypasses HTTP call."""
    loan_id = "LOAN_CACHE_HIT_01"
    s3_raw = tmp_path / "s3_raw" / loan_id
    s3_ext = tmp_path / "s3_extracted" / loan_id
    s3_raw.mkdir(parents=True, exist_ok=True)
    s3_ext.mkdir(parents=True, exist_ok=True)

    pdf_file = s3_raw / "pan.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 dummy")

    time.sleep(0.05)

    cached_json = s3_ext / "pan.json"
    cached_content = {"_raw_text": "cached extraction text", "pan_number": "ABCDE1234F"}
    cached_json.write_text(json.dumps(cached_content), encoding="utf-8")

    # Ensure mtime of cache is strictly greater than raw file
    curr_time = time.time() + 10
    os.utime(cached_json, (curr_time, curr_time))

    monkeypatch.setattr("pipeline.nodes.idp_scan.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("pipeline.nodes.idp_scan.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("pipeline.nodes.idp_scan.DISABLE_IDP_EXTRACTION_CACHE", False)

    def _fail_call(*args, **kwargs):
        raise AssertionError("HTTP service should not be called on cache hit")

    monkeypatch.setattr("pipeline.nodes.idp_scan._call_idp_service", _fail_call)

    state: PipelineState = {
        "loan_id": loan_id,
        "raw_doc_paths": {"pan.pdf": str(pdf_file)},
        "extracted_data": {},
        "errors": [],
        "node_history": [],
    }

    out_state = idp_scan(state)
    assert "pan" in out_state["extracted_data"]
    assert out_state["extracted_data"]["pan"]["_raw_text"] == "cached extraction text"


def test_idp_scan_cache_miss_calls_idp_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Unit: missing cache delegates to _call_idp_service and saves extracted result."""
    loan_id = "LOAN_CACHE_MISS_01"
    s3_raw = tmp_path / "s3_raw" / loan_id
    s3_ext = tmp_path / "s3_extracted" / loan_id
    s3_raw.mkdir(parents=True, exist_ok=True)
    s3_ext.mkdir(parents=True, exist_ok=True)

    pdf_file = s3_raw / "pan.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 dummy")

    monkeypatch.setattr("pipeline.nodes.idp_scan.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("pipeline.nodes.idp_scan.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")

    fresh_result = {"_raw_text": "fresh_ocr", "pan_number": "ABCDE1234F"}
    mock_call = MagicMock(return_value=fresh_result)
    mock_save = MagicMock()

    monkeypatch.setattr("pipeline.nodes.idp_scan._call_idp_service", mock_call)
    monkeypatch.setattr("pipeline.nodes.idp_scan.save_s3_extracted", mock_save)

    state: PipelineState = {
        "loan_id": loan_id,
        "raw_doc_paths": {"pan.pdf": str(pdf_file)},
        "extracted_data": {},
        "errors": [],
        "node_history": [],
    }

    out_state = idp_scan(state)
    assert mock_call.call_count == 1
    assert mock_save.called
    assert out_state["extracted_data"]["pan"] == fresh_result


def test_idp_scan_attaches_all_three_sidecars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Unit: sidecar JSON metadata files (face embeddings, DMS status, OTP audit) are parsed directly."""
    loan_id = "LOAN_SIDECARS_01"
    sidecar_dir = tmp_path / "sidecars"
    sidecar_dir.mkdir(parents=True, exist_ok=True)

    face_p = sidecar_dir / "face_embeddings.json"
    face_p.write_text(json.dumps({"embedding": [0.1, 0.2]}), encoding="utf-8")

    dms_p = sidecar_dir / "dms_status.json"
    dms_p.write_text(json.dumps({"status": "verified"}), encoding="utf-8")

    otp_p = sidecar_dir / "loan_agreement_otp_audit.json"
    otp_p.write_text(json.dumps({"otp_verified": True}), encoding="utf-8")

    state: PipelineState = {
        "loan_id": loan_id,
        "raw_doc_paths": {
            "face_embeddings.json": str(face_p),
            "dms_status.json": str(dms_p),
            "loan_agreement_otp_audit.json": str(otp_p),
        },
        "extracted_data": {},
        "errors": [],
        "node_history": [],
    }

    out_state = idp_scan(state)
    assert out_state["face_embeddings"] == {"embedding": [0.1, 0.2]}
    assert out_state["dms_status"] == {"status": "verified"}
    assert out_state["otp_audit"] == {"otp_verified": True}


def test_build_canonical_extracted_dict_output_contract():
    """Regression: verifies canonical dictionary adheres to required S3 storage contract."""
    parsed = ParsedDocument(
        document_id="DOC-PAN-01",
        source=DocumentSource(
            filename="pan.pdf",
            mime_type="application/pdf",
        ),
        pages=[PageInformation(page_number=1, width=600.0, height=800.0)],
        tables=[],
        elements=[],
        text="INCOME TAX DEPARTMENT GOVT OF INDIA",
        processing=ProcessingMetadata(
            document_id="DOC-PAN-01",
            processing_id="proc-DOC-PAN-01",
            file_type="pdf",
            mime_type="application/pdf",
            file_size_bytes=2048,
            page_count=1,
        ),
        custom_metadata={
            "llm_extracted_fields": {
                "pan_number": "ABCDE1234F",
                "name": "Jane Doe",
                "date_of_birth": "1990-01-01",
            }
        },
    )

    res = build_canonical_extracted_dict(parsed, doc_type="pan", doc_id="DOC-PAN-01")

    required_keys = [
        "_raw_text",
        "rawText",
        "_formatted_text",
        "formattedText",
        "_pages",
        "_elements_count",
        "_components",
        "_field_locations",
    ]
    for key in required_keys:
        assert key in res, f"Canonical output contract missing key: '{key}'"

    assert res["_raw_text"] == "INCOME TAX DEPARTMENT GOVT OF INDIA"
    assert res["pan_number"] == "ABCDE1234F"
    assert isinstance(res["_components"], dict)
    assert isinstance(res["_field_locations"], dict)


def test_celery_app_no_parsed_document_import():
    """Static analysis: ensures pipeline/celery_app.py does not import ParsedDocument or build_idp_result_from_parsed."""
    root_dir = Path(__file__).resolve().parent.parent
    celery_path = root_dir / "pipeline" / "celery_app.py"
    content = celery_path.read_text(encoding="utf-8")

    assert "from idp.models.document import ParsedDocument" not in content
    assert "build_idp_result_from_parsed" not in content
