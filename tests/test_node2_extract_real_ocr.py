import json
import pytest
from pathlib import Path
from pipeline.nodes.node2_extract import (
    node2_extract,
    _clean_numeric,
    _normalize_tenure_months,
    _map_doc_type_from_filename,
    _extract_fields_with_regex,
)
from pipeline.state import PipelineState


def test_clean_numeric():
    assert _clean_numeric("Rs. 5,00,000.00") == 500000.0
    assert _clean_numeric("INR 12,345") == 12345.0
    assert _clean_numeric(5000) == 5000.0
    assert _clean_numeric(None) is None
    assert _clean_numeric("N/A") is None


def test_normalize_tenure_months():
    assert _normalize_tenure_months("12 months") == 12
    assert _normalize_tenure_months("5 years") == 60
    assert _normalize_tenure_months("3 yrs") == 36
    assert _normalize_tenure_months(24) == 24
    assert _normalize_tenure_months(None) is None


def test_map_doc_type_from_filename():
    assert _map_doc_type_from_filename("loan_application_form.pdf") == "application_form"
    assert _map_doc_type_from_filename("loan_agreement.pdf") == "loan_agreement"
    assert _map_doc_type_from_filename("kfs.pdf") == "kfs"
    assert _map_doc_type_from_filename("sanction_letter.pdf") == "sanction_letter"
    assert _map_doc_type_from_filename("pan_card.pdf") == "kyc_pan"
    assert _map_doc_type_from_filename("aadhaar_front.pdf") == "kyc_address_proof"
    assert _map_doc_type_from_filename("disbursal_memo.pdf") == "disbursal_memo"


def test_extract_fields_with_regex_application_form():
    sample_text = """
    APPLICATION FORM FOR PERSONAL LOAN
    Application No: APP-2026-9901
    Applicant Name: Rahul Sharma
    Loan Amount: Rs. 5,00,000
    Tenure: 36 months
    PAN: ABCDE1234F
    Address: 402 Galaxy Heights, MG Road, Mumbai
    """
    fields = _extract_fields_with_regex(
        doc_type="application_form",
        full_text=sample_text,
        elements=[{"text": "Applicant Name: Rahul Sharma"}, {"text": "Loan Amount: Rs. 5,00,000"}]
    )

    assert fields["loan_amount"] == 500000.0
    assert fields["tenure_months"] == 36
    assert fields["applicant_name"] == "Rahul Sharma"
    assert fields["pan_number"] == "ABCDE1234F"
    assert "Galaxy Heights" in fields["address_text"]
    assert fields["application_id"] == "APP-2026-9901"


def test_extract_fields_with_regex_kfs_and_sanction():
    kfs_text = """
    KEY FACT STATEMENT (KFS)
    Loan Amount: Rs. 5,00,000
    Broken Period Interest: Rs. 1,250.50
    """
    kfs_fields = _extract_fields_with_regex("kfs", kfs_text, [])
    assert kfs_fields["loan_amount"] == 500000.0
    assert kfs_fields["funding_amount"] == 500000.0
    assert kfs_fields["broken_period_interest"] == 1250.50

    sanction_text = """
    SANCTION LETTER
    Sanctioned Amount: Rs. 5,00,000
    Tenure: 3 years
    Broken Period Interest: Rs. 1,250.50
    """
    sanction_fields = _extract_fields_with_regex("sanction_letter", sanction_text, [])
    assert sanction_fields["loan_amount"] == 500000.0
    assert sanction_fields["tenure_months"] == 36
    assert sanction_fields["broken_period_interest"] == 1250.50


def test_extract_fields_with_regex_disbursal_memo():
    memo_text = """
    DISBURSAL MEMORANDUM
    Application No: APP-9901
    Loan Closure No: CLS-8812
    Disbursal Amount: Rs. 4,85,000
    """
    memo_fields = _extract_fields_with_regex("disbursal_memo", memo_text, [])
    assert memo_fields["application_id"] == "APP-9901"
    assert memo_fields["closure_id"] == "CLS-8812"
    assert memo_fields["disbursal_amount"] == 485000.0


def test_node2_extract_fallback_and_sidecars(tmp_path):
    # Setup mock loan directory
    loan_id = "LOAN_TEST_OCR"
    state: PipelineState = {
        "loan_id": loan_id,
        "los_data": {"loan_id": loan_id, "applicant_name": "Test User"},
        "raw_doc_paths": {},
        "extracted_data": {},
        "face_embeddings": {},
        "dms_status": {},
        "otp_audit": {},
        "comparison_results": [],
        "subnode_rollups": {},
        "compiled_report": {},
        "scorecard": {},
        "retry_count": 0,
        "checker_result": {},
        "errors": [],
        "node_history": ["fetch"],
    }

    result = node2_extract(state)
    assert result["loan_id"] == loan_id
    assert "extract" in result["node_history"]
    assert isinstance(result["extracted_data"], dict)
    assert isinstance(result["errors"], list)


def test_node2_extract_structured_components_persistence(tmp_path, monkeypatch):
    import pipeline.nodes.node2_extract as node2_mod
    from pipeline.storage import write_json, read_json

    loan_id = "LOAN_TEST_COMPONENTS"
    test_extracted_dir = tmp_path / "s3_extracted"
    monkeypatch.setattr(node2_mod, "S3_EXTRACTED_DIR", test_extracted_dir)

    # Simulate extracted data containing structured components
    state: PipelineState = {
        "loan_id": loan_id,
        "los_data": {"loan_id": loan_id},
        "raw_doc_paths": {},
        "extracted_data": {
            "application_form": {
                "applicant_name": "Vikram Aditya Rao",
                "loan_amount": 500000.0,
                "_components": {
                    "document_type": "application_form",
                    "key_values": {"Applicant Name": "Vikram Aditya Rao", "Loan Amount": "500,000"},
                    "tables": [{"id": "tbl-1", "headers": ["Item", "Cost"], "rows": [["Fee", "1000"]]}],
                    "paragraphs": [{"id": "p-1", "text": "Terms and conditions apply.", "page_number": 1}]
                }
            }
        },
        "face_embeddings": {},
        "dms_status": {},
        "otp_audit": {},
        "comparison_results": [],
        "subnode_rollups": {},
        "compiled_report": {},
        "scorecard": {},
        "retry_count": 0,
        "checker_result": {},
        "errors": [],
        "node_history": ["fetch"],
    }

    result = node2_extract(state)

    # 1. Standard original file exists untouched
    orig_file = test_extracted_dir / loan_id / "application_form.json"
    assert orig_file.exists()
    assert read_json(orig_file)["applicant_name"] == "Vikram Aditya Rao"

    # 2. Dedicated new structured components file exists with clean key_values & tables
    struct_file = test_extracted_dir / loan_id / "application_form_structured.json"
    assert struct_file.exists()
    struct_data = read_json(struct_file)
    assert struct_data["key_values"]["Applicant Name"] == "Vikram Aditya Rao"
    assert len(struct_data["tables"]) == 1
    assert struct_data["tables"][0]["headers"] == ["Item", "Cost"]
    assert len(struct_data["paragraphs"]) == 1

