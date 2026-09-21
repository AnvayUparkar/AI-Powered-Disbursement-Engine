import io
import uuid
from fastapi.testclient import TestClient
from app.main import app
from app.services.document_registry import DocumentRegistry, document_registry

client = TestClient(app)


def test_document_registry_unit():
    registry = DocumentRegistry()
    docs = registry.list_all()
    assert len(docs) > 0
    assert any(d["type"] in ("Sanction Letter", "Key Fact Statement (KFS)", "PAN Card", "Aadhaar", "Aadhaar XML", "Application Form", "Loan Agreement") for d in docs)

    # Register an upload
    doc_id = f"DOC-TEST-{uuid.uuid4().hex[:6].upper()}"
    parsed_mock = {
        "document_id": doc_id,
        "pages": [{"page_number": 1}],
        "custom_metadata": {
            "llm_extracted_fields": {"loan_amount": "750000"},
            "ocr_tokens": [{"text": "750000", "confidence": 0.98}],
        },
        "elements": [
            {
                "id": "e-1",
                "text": "Loan Amount: 750000",
                "confidence": 0.98,
                "page_number": 1,
                "source": "ocr"
            }
        ],
        "tables": [],
        "processing": {"vlm_used": False, "file_size_bytes": 10240}
    }

    record = registry.register_uploaded_document(
        doc_id=doc_id,
        filename="custom_sanction.pdf",
        doc_type="Sanction Letter",
        case_id="LOAN_001",
        file_size_bytes=10240,
        parsed_result=parsed_mock
    )

    assert record["id"] == doc_id
    assert record["caseId"] == "LOAN_001"
    assert record["type"] == "Sanction Letter"
    assert len(record["extractedFields"]) >= 1
    assert any("750000" in str(f.get("value")) for f in record["extractedFields"])

    # Query registry
    fetched = registry.get_by_id(doc_id)
    assert fetched is not None
    assert fetched["name"] == "custom_sanction.pdf"

    # Filter by case
    case_docs = registry.list_all(case_id="LOAN_001")
    assert any(d["id"] == doc_id for d in case_docs)


def test_api_upload_and_immediate_listing():
    """
    End-to-End Integration Test:
    Upload a document via POST /api/v1/documents/upload and assert it is immediately queryable in GET /api/documents.
    """
    test_doc_id = f"DOC-INT-{uuid.uuid4().hex[:6].upper()}"
    file_bytes = b"%PDF-1.4 Mock Sanction Letter Content\nLoan Amount: 500000"
    files = {
        "file": ("e2e_sanction_document.pdf", io.BytesIO(file_bytes), "application/pdf")
    }
    data = {
        "document_id": test_doc_id,
        "case_id": "LOAN_002",
        "doc_type": "Sanction Letter"
    }

    # 1. Upload
    upload_res = client.post("/api/v1/documents/upload", files=files, data=data)
    assert upload_res.status_code == 200

    # 2. Query document list from GET /api/documents
    list_res = client.get("/api/documents")
    assert list_res.status_code == 200
    docs = list_res.json()
    assert any(d["id"] == test_doc_id for d in docs), f"Uploaded doc {test_doc_id} not found in GET /api/documents"

    # 3. Filter by case_id
    case_filter_res = client.get("/api/documents?caseId=LOAN_002")
    assert case_filter_res.status_code == 200
    case_docs = case_filter_res.json()
    assert any(d["id"] == test_doc_id for d in case_docs)

    # 4. Search by filename
    search_res = client.get("/api/documents?query=e2e_sanction")
    assert search_res.status_code == 200
    search_docs = search_res.json()
    assert any(d["id"] == test_doc_id for d in search_docs)

    # 5. Get document details by ID
    detail_res = client.get(f"/api/documents/{test_doc_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["id"] == test_doc_id
    assert detail["name"] == "e2e_sanction_document.pdf"
    assert detail["type"] == "Sanction Letter"
    assert "extractedFields" in detail
    assert "processingSteps" in detail


def test_api_documents_types():
    res = client.get("/api/documents/types")
    assert res.status_code == 200
    types = res.json()
    assert isinstance(types, list)
    assert any(t in types for t in ("Sanction Letter", "KFS", "Aadhaar", "Aadhaar XML", "Application Form"))


def test_case_document_raw_text_and_fields():
    registry = DocumentRegistry()
    registry.register_uploaded_document(
        doc_id="doc-LOAN_001-pan",
        filename="PAN.pdf",
        doc_type="PAN",
        case_id="LOAN_001",
        parsed_result={
            "document_id": "doc-LOAN_001-pan",
            "text": "Permanent Account Number: ABCDE1234F",
            "custom_metadata": {"llm_extracted_fields": {"pan_number": "ABCDE1234F"}},
        }
    )
    doc = registry.get_by_id("doc-LOAN_001-pan")
    assert doc is not None, "doc-LOAN_001-pan should be found in registry"
    assert "rawText" in doc
    assert len(doc["rawText"]) > 0
    assert len(doc["extractedFields"]) > 0
    assert any("ABCDE1234F" in str(f.get("value")) for f in doc["extractedFields"])


def test_idp_fallback_route_for_case_documents():
    document_registry.register_uploaded_document(
        doc_id="doc-LOAN_001-pan",
        filename="PAN.pdf",
        doc_type="PAN",
        case_id="LOAN_001",
        parsed_result={
            "document_id": "doc-LOAN_001-pan",
            "text": "Permanent Account Number: ABCDE1234F",
        }
    )
    res = client.get("/api/v1/documents/doc-LOAN_001-pan")
    assert res.status_code == 200, f"Expected 200 OK from /api/v1/documents/doc-LOAN_001-pan, got {res.status_code}"
    data = res.json()
    assert data.get("id") == "doc-LOAN_001-pan"
    assert "rawText" in data


def test_canonical_alias_resolution_for_synthetic_evidence_links():
    """Happy path & edge cases for synthetic evidence IDs (e.g. doc-LOAN_001-sanction)."""
    registry = document_registry
    registry.register_uploaded_document(
        doc_id="DOC-LOAN_001-SANC",
        filename="sanction_letter.pdf",
        doc_type="Sanction Letter",
        case_id="LOAN_001",
    )

    # 1. doc-LOAN_001-sanction resolves to sanction_letter.pdf
    doc = registry.get_by_id("doc-LOAN_001-sanction")
    assert doc is not None, "doc-LOAN_001-sanction should resolve to sanction_letter"
    assert "sanction" in doc["name"].lower()
    assert doc["caseId"] == "LOAN_001"

    # 2. Case-insensitivity in resolution
    doc_ci = registry.get_by_id("doc-loan_001-SANCTION")
    assert doc_ci is not None
    assert doc_ci["id"] == doc["id"]

    # 3. Dynamic upload with numbered filename (e.g. sanction_letter_1.PDF for LOAN_004)
    doc_id = f"DOC-LOAN_004-{uuid.uuid4().hex[:4].upper()}"
    registry.register_uploaded_document(
        doc_id=doc_id,
        filename="sanction_letter_1.PDF",
        doc_type="Sanction Letter",
        case_id="LOAN_004",
        file_size_bytes=37820,
    )

    resolved = registry.get_by_id("doc-LOAN_004-sanction")
    assert resolved is not None, "doc-LOAN_004-sanction must resolve dynamically uploaded sanction_letter_1.PDF"
    assert resolved["caseId"] == "LOAN_004"
    assert resolved["name"] == "sanction_letter_1.PDF"

    # 4. HTTP API integration via /api/v1/documents/doc-LOAN_004-sanction
    api_res = client.get("/api/v1/documents/doc-LOAN_004-sanction")
    assert api_res.status_code == 200
    assert api_res.json().get("name") == "sanction_letter_1.PDF"


def test_canonical_alias_resolution_failure_modes():
    """Error modes: non-existent cases and invalid types return None / 404."""
    registry = DocumentRegistry()

    # Non-existent case
    assert registry.get_by_id("doc-NONEXISTENT_999-sanction") is None

    # Non-existent document type for existing case
    assert registry.get_by_id("doc-LOAN_001-nonexistent_type_xyz") is None

    # HTTP API returns 404
    api_res = client.get("/api/v1/documents/doc-LOAN_001-nonexistent_type_xyz")
    assert api_res.status_code == 404


def test_dynamic_ocr_confidence_calculation():
    """Validates that document confidence and processing step confidence are mathematical averages of real OCR telemetry."""
    registry = DocumentRegistry()
    doc_id = f"DOC-TEST-DYNCONF-{uuid.uuid4().hex[:6].upper()}"

    parsed_mock = {
        "document_id": doc_id,
        "pages": [{"page_number": 1}],
        "custom_metadata": {
            "ocr_tokens": [
                {"text": "Applicant", "confidence": 0.85},
                {"text": "Name", "confidence": 0.75},
                {"text": "John", "confidence": 0.90},
            ]
        },
        "elements": [
            {
                "id": "e-1",
                "text": "Applicant Name: John",
                "confidence": 0.85,
                "page_number": 1,
                "source": "ocr",
            }
        ],
        "tables": [
            {
                "id": "tbl-1",
                "num_rows": 2,
                "num_cols": 2,
                "page_number": 1,
                "cells": [
                    {"text": "Header1", "confidence": 0.90},
                    {"text": "Header2", "confidence": 0.80},
                ],
            }
        ],
        "processing": {"vlm_used": False, "file_size_bytes": 10240},
    }

    record = registry.register_uploaded_document(
        doc_id=doc_id,
        filename="custom_test_doc.pdf",
        doc_type="Sanction Letter",
        case_id="LOAN_DYN_01",
        file_size_bytes=10240,
        parsed_result=parsed_mock,
    )

    # Expected dynamic average from tokens [85, 75, 90] and elements [85] -> ~83.8%
    assert record["confidence"] > 0.0
    assert record["confidence"] != 97.5  # Not the old hardcoded 97.5%
    assert record["confidence"] != 96.5  # Not the old hardcoded 96.5%
    assert record["confidence"] != 91.0  # Not the old hardcoded 91.0%
    assert 80.0 <= record["confidence"] <= 90.0

    # Table cell confidence check
    tbl_field = next(f for f in record["extractedFields"] if f["type"] == "table")
    assert tbl_field["confidence"] == 85.0  # mean([90.0, 80.0])

    # PaddleOCR step must match dynamic confidence
    step_ocr = next(s for s in record["processingSteps"] if s["component"] == "PaddleOCR")
    assert step_ocr["confidence"] == record["confidence"]

    # Pending document must yield 0.0% confidence
    pending_doc_id = f"DOC-TEST-PENDING-{uuid.uuid4().hex[:6].upper()}"
    pending_rec = registry.register_uploaded_document(
        doc_id=pending_doc_id,
        filename="pending_doc.pdf",
        doc_type="Sanction Letter",
        case_id="LOAN_DYN_01",
        status="PENDING",
    )
    assert pending_rec["confidence"] == 0.0
    pending_ocr_step = next(s for s in pending_rec["processingSteps"] if s["component"] == "PaddleOCR")
    assert pending_ocr_step["confidence"] == 0.0


def test_case_document_canonical_enrichment():
    """Verify enrich_document_record resolves canonical doc_type aadhaar.json for Aadhar Card.pdf."""
    from app.services.registry.case_scanner import enrich_document_record
    dummy_doc = {
        "id": "DOC-317628",
        "name": "Aadhar Card.pdf",
        "type": "Aadhaar",
        "caseId": "APPL00343265",
        "ocrStatus": "PENDING",
        "extractionStatus": "PENDING",
        "status": "pending",
        "extractedFields": [],
        "rawText": "",
    }
    enriched = enrich_document_record(dummy_doc)
    assert enriched["ocrStatus"] == "COMPLETED"
    assert enriched["extractionStatus"] == "COMPLETED"
    assert len(enriched["rawText"]) > 0
    assert len(enriched["extractedFields"]) > 0
    assert any("Aadhaar Number" in f.get("name", "") or "Applicant Name" in f.get("name", "") for f in enriched["extractedFields"])


def test_dynamic_reconciliation_replaces_pending_with_completed():
    """Verify merge_and_deduplicate updates PENDING dynamic record when completed case doc is present."""
    from app.services.registry.dedup import merge_and_deduplicate

    dynamic_pending = [
        {
            "id": "DOC-317628",
            "name": "Aadhar Card.pdf",
            "type": "Aadhaar",
            "caseId": "APPL00343265",
            "ocrStatus": "PENDING",
            "extractionStatus": "PENDING",
            "status": "pending",
            "uploadedTimestamp": 100.0,
        }
    ]
    case_completed = [
        {
            "id": "APPL00343265_aadhaar",
            "name": "Aadhar Card.pdf",
            "type": "Aadhaar",
            "caseId": "APPL00343265",
            "ocrStatus": "COMPLETED",
            "extractionStatus": "COMPLETED",
            "status": "processed",
            "confidence": 98.5,
            "rawText": "MOCK AADHAAR OCR TEXT",
            "extractedFields": [{"id": "f1", "name": "Aadhaar Number", "value": "1234 5678 9012"}],
        }
    ]

    merged = merge_and_deduplicate(dynamic_pending, case_completed)
    assert len(merged) == 1
    record = merged[0]
    assert record["id"] == "DOC-317628"
    assert record["ocrStatus"] == "COMPLETED"
    assert record["extractionStatus"] == "COMPLETED"
    assert record["rawText"] == "MOCK AADHAAR OCR TEXT"
    assert len(record["extractedFields"]) == 1


def test_api_upload_mints_deterministic_canonical_id_for_cases():
    """Verify direct upload to a case mints {case_id}_{canon} deterministic ID instead of random DOC-XXXXXX."""
    file_bytes = b"%PDF-1.4 Test Aadhaar Content"
    files = {
        "file": ("Aadhar Card.pdf", io.BytesIO(file_bytes), "application/pdf")
    }
    data = {
        "case_id": "APPL00343265",
        "doc_type": "Aadhaar",
        "run_idp": "false",
    }
    upload_res = client.post("/api/v1/documents/upload", files=files, data=data)
    assert upload_res.status_code == 200
    res_data = upload_res.json()
    assert res_data["document_id"] == "APPL00343265_aadhaar"
    assert res_data["status"] == "UPLOADED"





