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
    assert any(d["type"] == "Sanction Letter" for d in docs)

    # Register an upload
    doc_id = f"DOC-TEST-{uuid.uuid4().hex[:6].upper()}"
    parsed_mock = {
        "document_id": doc_id,
        "pages": [{"page_number": 1}],
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
    assert any(f["name"] == "Loan Amount" and f["value"] == "750000" for f in record["extractedFields"])

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
    assert "Sanction Letter" in types
    assert "Application Form" in types


def test_case_document_raw_text_and_fields():
    registry = DocumentRegistry()
    doc = registry.get_by_id("doc-LOAN_001-pan")
    assert doc is not None, "doc-LOAN_001-pan should be found in registry"
    assert "rawText" in doc
    assert len(doc["rawText"]) > 0
    assert len(doc["extractedFields"]) > 0
    assert any("ABCDE1234F" in str(f.get("value")) for f in doc["extractedFields"])


def test_idp_fallback_route_for_case_documents():
    res = client.get("/api/v1/documents/doc-LOAN_001-pan")
    assert res.status_code == 200, f"Expected 200 OK from /api/v1/documents/doc-LOAN_001-pan, got {res.status_code}"
    data = res.json()
    assert data.get("id") == "doc-LOAN_001-pan"
    assert "rawText" in data


def test_canonical_alias_resolution_for_synthetic_evidence_links():
    """Happy path & edge cases for synthetic evidence IDs (e.g. doc-LOAN_001-sanction)."""
    registry = document_registry

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



