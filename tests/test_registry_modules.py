"""Unit tests for decoupled document registry modules."""
import pytest
from app.services.registry.dedup import (
    SINGLETON_TYPES,
    filter_documents,
    get_all_distinct_types,
    merge_and_deduplicate,
)
from app.services.registry.normalizer import (
    build_default_extracted_fields,
    build_default_processing_steps,
    normalize_uploaded_record,
    parse_extracted_fields,
)
from app.services.registry.resolver import (
    guess_doc_type,
    normalize_doc_name,
    resolve_synthetic_alias,
)
from app.services.registry.case_scanner import enrich_document_record, invalidate_case_cache


def test_normalizer_build_defaults():
    steps = build_default_processing_steps("DOC-123")
    assert len(steps) == 2
    assert steps[0]["component"] == "Docling"
    assert steps[1]["component"] == "PaddleOCR"

    fields = build_default_extracted_fields("DOC-123", "sample.pdf", "Verified & Indexed", is_status=True)
    assert len(fields) == 2
    assert fields[0]["name"] == "Document Name"
    assert fields[0]["value"] == "sample.pdf"
    assert fields[1]["name"] == "Processing Status"
    assert fields[1]["value"] == "Verified & Indexed"


def test_normalizer_parse_extracted_fields():
    doc_id = "DOC-PARSED-1"
    parsed_result = {
        "elements": [
            {"id": "e-1", "text": "Annual Income: 1200000", "confidence": 0.96, "page_number": 1, "source": "ocr"},
            {"id": "e-2", "text": "EMPLOYMENT VERIFICATION", "type": "heading", "confidence": 0.99, "page_number": 1},
        ],
        "tables": [
            {
                "id": "t-1",
                "page_number": 1,
                "num_rows": 3,
                "num_cols": 2,
                "headers": ["Item", "Amount"],
                "rows_raw": [["Salary", "100000"]],
            }
        ],
        "custom_metadata": {
            "field_locations": {
                "applicant_name": {
                    "bbox": [10.0, 20.0, 100.0, 40.0],
                    "location_status": "resolved",
                    "confidence": 0.98,
                    "matched_text": "John Doe",
                }
            }
        }
    }
    llm_meta = {"applicant_name": "John Doe"}

    fields = parse_extracted_fields(doc_id, parsed_result, llm_meta)
    assert len(fields) == 4

    # LLM field
    llm_field = next(f for f in fields if f["id"] == "llm-applicant_name")
    assert llm_field["value"] == "John Doe"
    assert llm_field["bbox"] == [10.0, 20.0, 100.0, 40.0]
    assert llm_field["locationStatus"] == "resolved"

    # Key-value element
    kv_field = next(f for f in fields if f["id"] == "e-1")
    assert kv_field["name"] == "Annual Income"
    assert kv_field["value"] == "1200000"

    # Heading element
    head_field = next(f for f in fields if f["id"] == "e-2")
    assert head_field["name"] == "Heading"
    assert head_field["value"] == "EMPLOYMENT VERIFICATION"

    # Table element
    tbl_field = next(f for f in fields if f["id"] == "t-1")
    assert tbl_field["type"] == "table"
    assert "3 rows x 2 cols" in tbl_field["value"]


def test_normalizer_uploaded_record():
    record = normalize_uploaded_record(
        doc_id="DOC-UP-1",
        filename="bank_statement.pdf",
        detected_type="Bank Statement",
        assoc_case="LOAN_100",
        file_size_bytes=20480,
    )
    assert record["id"] == "DOC-UP-1"
    assert record["name"] == "bank_statement.pdf"
    assert record["caseId"] == "LOAN_100"
    assert record["sizeKb"] == 20
    assert record["ocrStatus"] == "COMPLETED"
    assert len(record["extractedFields"]) >= 2


def test_resolver_guess_doc_type():
    assert guess_doc_type("pan_card.jpg") in ("PAN", "PAN Card")
    assert "Aadhaar" in guess_doc_type("aadhaar_offline.pdf")
    assert guess_doc_type("sanction_letter_final.pdf") == "Sanction Letter"
    assert guess_doc_type("kfs_disclosure.pdf") in ("KFS", "Key Fact Statement (KFS)")
    assert guess_doc_type("random_unknown_file.xyz") == "Random Unknown File"


def test_resolver_normalize_doc_name():
    assert normalize_doc_name("doc-LOAN_001_aadhaar_front.pdf") == "front.pdf"
    assert normalize_doc_name("aadhar_scan_copy.pdf") == "scan_copy.pdf"
    assert normalize_doc_name("adhar_back.pdf") == "back.pdf"


def test_resolver_resolve_synthetic_alias():
    candidates = [
        {"id": "doc-LOAN_001-pan", "caseId": "LOAN_001", "type": "PAN Card", "name": "pan.pdf"},
        {"id": "doc-LOAN_001-sanction", "caseId": "LOAN_001", "type": "Sanction Letter", "name": "sanction_letter.pdf"},
    ]

    resolved = resolve_synthetic_alias("doc-LOAN_001-sanction", candidates)
    assert resolved is not None
    assert resolved["id"] == "doc-LOAN_001-sanction"

    # Case insensitive
    resolved_ci = resolve_synthetic_alias("doc-loan_001-SANCTION", candidates)
    assert resolved_ci is not None

    # Non-matching
    assert resolve_synthetic_alias("doc-LOAN_999-sanction", candidates) is None
    assert resolve_synthetic_alias("doc-LOAN_001-nonexistent", candidates) is None
    assert resolve_synthetic_alias("invalid-format", candidates) is None


def test_dedup_merge_and_filter():
    dynamic = [
        {"id": "DOC-D1", "caseId": "LOAN_001", "type": "Sanction Letter", "name": "new_sanction.pdf"},
        {"id": "DOC-D2", "caseId": "LOAN_001", "type": "Aadhaar", "name": "aadhaar_front.pdf"},
    ]
    case_docs = [
        {"id": "doc-LOAN_001-sanction_old", "caseId": "LOAN_001", "type": "Sanction Letter", "name": "old_sanction.pdf"},
        {"id": "doc-LOAN_001-pan", "caseId": "LOAN_001", "type": "PAN Card", "name": "pan.pdf"},
    ]

    merged = merge_and_deduplicate(dynamic, case_docs)
    # The old sanction letter must be superseded by the new dynamic upload
    sanctions = [d for d in merged if d["type"] == "Sanction Letter"]
    assert len(sanctions) == 1
    assert sanctions[0]["id"] == "DOC-D1"

    # Filtering tests
    by_case = filter_documents(merged, case_id="LOAN_001")
    assert len(by_case) == 3

    by_type = filter_documents(merged, doc_type="Sanction Letter")
    assert len(by_type) == 1
    assert by_type[0]["id"] == "DOC-D1"

    by_query = filter_documents(merged, query="front")
    assert len(by_query) == 1
    assert by_query[0]["id"] == "DOC-D2"


def test_get_distinct_types():
    dynamic_types = {"Custom Appraisal", "PAN"}
    distinct = get_all_distinct_types(dynamic_types)
    assert "Custom Appraisal" in distinct
    assert "Sanction Letter" in distinct
    assert "Application Form" in distinct


def test_case_scanner_enrich_noop_when_formatted():
    doc = {
        "id": "DOC-DONE",
        "caseId": "LOAN_001",
        "name": "sample.pdf",
        "formattedText": '{"applicant_name": "Alice"}',
        "extractedFields": []
    }
    enriched = enrich_document_record(doc)
    assert enriched["formattedText"] == '{"applicant_name": "Alice"}'
