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

    # 2. No extra _structured.json or other intermediate json files should be formed
    struct_file = test_extracted_dir / loan_id / "application_form_structured.json"
    assert not struct_file.exists(), "Only the canonical LLM template JSON should be created"


def test_format_template_json_exact_22_field_ordering_and_defaults():
    from pipeline.nodes.llm_field_extractor import TEMPLATE_FIELDS, format_template_json

    assert len(TEMPLATE_FIELDS) == 22

    # 1. Empty input produces all 22 keys in order with None/False
    empty_res = format_template_json({})
    assert list(empty_res.keys()) == list(TEMPLATE_FIELDS)
    assert empty_res["aadhaar_xml_present"] is False
    assert empty_res["loan_agreement_present"] is False
    assert empty_res["loan_agreement_signed"] is False
    for k in TEMPLATE_FIELDS:
        if k not in {"aadhaar_xml_present", "loan_agreement_present", "loan_agreement_signed"}:
            assert empty_res[k] is None

    # 2. None input handled safely
    none_res = format_template_json(None)
    assert list(none_res.keys()) == list(TEMPLATE_FIELDS)

    # 3. User sample document JSON strictly reproduced
    user_sample = {
        "applicant_name": "PRAKASH KHATRI",
        "fathers_name": "Gyan Chand Khatri",
        "dob": "22/06/1976",
        "mobile_no": 9166202777,
        "gender": "Male",
        "aadhaar_number": "XXXXXXXX5552",
        "pan_number": None,
        "address": "30/105, Sindhi Colony, Jhulelal Mandir ke pass, Sanganer, Jaipur, Rajasthan, 302029",
        "current_address": None,
        "bank_account_no": None,
        "type_of_account": None,
        "loan_amount": None,
        "loan_validity": None,
        "loan_type": None,
        "application_no": None,
        "application_date": None,
        "BPI": None,
        "irr_percent": None,
        "emi": None,
        "aadhaar_xml_present": False,
        "loan_agreement_present": False,
        "loan_agreement_signed": False,
    }
    sample_res = format_template_json(user_sample)
    assert sample_res == user_sample
    assert list(sample_res.keys()) == list(TEMPLATE_FIELDS)

    # 4. Alias normalization works seamlessly
    alias_input = {
        "account_no": "987654321012",
        "loan_no": "LOAN-APP-883",
        "pan": "ABCDE1234F",
        "aadhaar": "999988887777",
        "sanctioned_amount": 750000.0,
        "tenure_months": 36,
        "bpi": 1250.0,
        "roi": 11.5,
        "address_text": "123 Street, City",
        "loan_agreement_signed": True,
    }
    norm_res = format_template_json(alias_input)
    assert norm_res["bank_account_no"] == "987654321012"
    assert norm_res["application_no"] == "LOAN-APP-883"
    assert norm_res["pan_number"] == "ABCDE1234F"
    assert norm_res["aadhaar_number"] == "999988887777"
    assert norm_res["loan_amount"] == 750000.0
    assert norm_res["loan_validity"] == 36
    assert norm_res["BPI"] == 1250.0
    assert norm_res["irr_percent"] == 11.5
    assert norm_res["address"] == "123 Street, City"
    assert norm_res["loan_agreement_signed"] is True
    assert list(norm_res.keys()) == list(TEMPLATE_FIELDS)


def test_node2_extract_forms_and_saves_different_template_jsons_for_every_document(tmp_path, monkeypatch):
    """Verifies that every distinct document processed produces its own .json file,

    all strictly adhering to the exact 22-field template schema.
    """
    import pipeline.nodes.node2_extract as node2_mod
    from pipeline.nodes.llm_field_extractor import TEMPLATE_FIELDS
    from pipeline.storage import read_json

    loan_id = "LOAN_MULTI_DOC_TEST"
    test_extracted_dir = tmp_path / "s3_extracted"
    monkeypatch.setattr(node2_mod, "S3_EXTRACTED_DIR", test_extracted_dir)

    # Simulate multi-document extracted data (Aadhaar, PAN, KFS, Loan Agreement)
    state: PipelineState = {
        "loan_id": loan_id,
        "los_data": {"loan_id": loan_id},
        "raw_doc_paths": {},
        "extracted_data": {
            "kyc_address_proof": {
                "applicant_name": "PRAKASH KHATRI",
                "fathers_name": "Gyan Chand Khatri",
                "dob": "22/06/1976",
                "mobile_no": 9166202777,
                "gender": "Male",
                "aadhaar_number": "XXXXXXXX5552",
                "address": "30/105, Sindhi Colony, Jaipur",
                "_raw_text": "GOVERNMENT OF INDIA AADHAAR PRAKASH KHATRI",
            },
            "kyc_pan": {
                "applicant_name": "PRAKASH KHATRI",
                "fathers_name": "Gyan Chand Khatri",
                "dob": "22/06/1976",
                "pan_number": "ABCDE1234F",
                "_raw_text": "INCOME TAX DEPARTMENT PERMANENT ACCOUNT NUMBER",
            },
            "kfs": {
                "loan_amount": 94111.0,
                "loan_validity": "36 Months",
                "emi": 3586.0,
                "application_no": "2638871426000308_1",
                "irr_percent": 14.5,
                "_raw_text": "Key Fact Sheet Application Number: 2638871426000308_1",
            },
            "loan_agreement": {
                "applicant_name": "PRAKASH KHATRI",
                "loan_amount": 94111.0,
                "loan_agreement_present": True,
                "loan_agreement_signed": True,
                "_raw_text": "LOAN AGREEMENT eSigned by PRAKASH KHATRI",
            },
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

    loan_out_dir = test_extracted_dir / loan_id
    assert loan_out_dir.exists()

    expected_docs = ["kyc_address_proof", "kyc_pan", "kfs", "loan_agreement"]

    for doc_name in expected_docs:
        doc_json_file = loan_out_dir / f"{doc_name}.json"
        assert doc_json_file.exists(), f"Expected {doc_name}.json to be saved"

        content = read_json(doc_json_file)

        # 1. Exact 22 keys in exact template order
        assert list(content.keys()) == list(TEMPLATE_FIELDS)

        # 2. Internal metadata keys must NOT be present in canonical json
        for internal_k in ("_raw_text", "rawText", "_components", "_pages", "_formatted_text"):
            assert internal_k not in content

        # 3. Document-specific field assertions
        if doc_name == "kyc_address_proof":
            assert content["applicant_name"] == "PRAKASH KHATRI"
            assert content["aadhaar_number"] == "XXXXXXXX5552"
            assert content["pan_number"] is None
            assert content["loan_amount"] is None
            assert content["loan_agreement_signed"] is False
        elif doc_name == "kyc_pan":
            assert content["applicant_name"] == "PRAKASH KHATRI"
            assert content["pan_number"] == "ABCDE1234F"
            assert content["aadhaar_number"] is None
            assert content["loan_amount"] is None
        elif doc_name == "kfs":
            assert content["loan_amount"] == 94111.0
            assert content["application_no"] == "2638871426000308_1"
            assert content["emi"] == 3586.0
            assert content["irr_percent"] == 14.5
            assert content["pan_number"] is None
        elif doc_name == "loan_agreement":
            assert content["loan_agreement_present"] is True
            assert content["loan_agreement_signed"] is True
            assert content["loan_amount"] == 94111.0
            assert content["dob"] is None

