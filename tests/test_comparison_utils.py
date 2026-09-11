import pytest

from pipeline.engines.comparison import (
    clean_aadhaar,
    clean_id,
    clean_numeric,
    clean_string,
    compare_aadhaar,
    compare_bpi_doc_to_doc,
    compare_tenure,
    compute_tfidf_cosine,
    extract_field_value,
    is_masked_aadhaar,
    mask_aadhaar,
    normalize_date,
    normalize_tenure_months,
    resolve_doc_data,
    run_field_checks,
)


def test_clean_numeric():
    assert clean_numeric("500000") == 500000.0
    assert clean_numeric("₹ 5,00,000.50") == 500000.50
    assert clean_numeric(45000) == 45000.0
    assert clean_numeric(None) is None
    assert clean_numeric("invalid") is None
    assert clean_numeric("") is None


def test_clean_id():
    assert clean_id(" abcde 1234f ") == "ABCDE1234F"
    assert clean_id(None) is None
    assert clean_id("") is None


def test_clean_string():
    assert clean_string("  Hello World  ") == "Hello World"
    assert clean_string(None) == ""


def test_normalize_date():
    assert normalize_date("2024-01-15") == "2024-01-15"
    assert normalize_date("15-01-2024") == "2024-01-15"
    assert normalize_date("15/01/2024") == "2024-01-15"
    assert normalize_date("22061976") == "1976-06-22"
    assert normalize_date("19760622") == "1976-06-22"
    assert normalize_date(None) is None
    assert normalize_date("Unparseable Date") == "unparseable date"


def test_compute_tfidf_cosine():
    text1 = "123 MG Road Bangalore Karnataka 560001"
    text2 = "123 MG Road Bangalore Karnataka 560001"
    assert compute_tfidf_cosine(text1, text2) == 1.0
    assert compute_tfidf_cosine("", text2) == 0.0
    assert compute_tfidf_cosine(text1, "") == 0.0


def test_resolve_doc_data():
    extracted = {
        "kyc_pan": {"pan_number": "ABCDE1234F"},
        "aadhaar": {"aadhaar_number": "123456789012"},
    }
    assert resolve_doc_data(extracted, "pan") == {"pan_number": "ABCDE1234F"}
    assert resolve_doc_data(extracted, "aadhaar") == {"aadhaar_number": "123456789012"}
    assert resolve_doc_data(extracted, "kfs") is None
    assert resolve_doc_data({}, "pan") is None


def test_run_field_checks_happy_path():
    doc_data = {"applicant_name": "Rajesh Sharma", "mobile_no": "9876543210"}
    los_data = {"applicant_name": "Rajesh Sharma", "applicant_mobile_no": "9876543210"}
    checks = [
        {"doc_field": "applicant_name", "los_field": "applicant_name", "method": "jaro_winkler"},
        {"doc_field": "mobile_no", "los_field": "applicant_mobile_no", "method": "exact_string"},
    ]
    records = run_field_checks("aadhaar", doc_data, los_data, checks, "LOAN_001", "test_node")
    assert len(records) == 2
    assert records[0]["match_status"] == "MATCH"
    assert records[1]["match_status"] == "MATCH"
    assert records[0]["confidence"] == 1.0


def test_run_field_checks_missing_doc_emits_not_found():
    los_data = {"applicant_name": "Rajesh Sharma"}
    checks = [
        {"doc_field": "applicant_name", "los_field": "applicant_name", "method": "jaro_winkler"},
    ]
    records = run_field_checks("aadhaar", None, los_data, checks, "LOAN_001", "test_node")
    assert len(records) == 1
    assert records[0]["match_status"] == "NOT_FOUND"
    assert records[0]["confidence"] == 0.0
    assert "not found in extracted data" in records[0]["notes"]


def test_run_field_checks_missing_field_in_doc_emits_not_found():
    doc_data = {"other_field": "foo"}
    los_data = {"applicant_name": "Rajesh Sharma"}
    checks = [
        {"doc_field": "applicant_name", "los_field": "applicant_name", "method": "jaro_winkler"},
    ]
    records = run_field_checks("aadhaar", doc_data, los_data, checks, "LOAN_001", "test_node")
    assert len(records) == 1
    assert records[0]["match_status"] == "NOT_FOUND"
    assert records[0]["confidence"] == 0.0


def test_run_field_checks_presence_only():
    doc_data_yes = {"customer_consent": "YES"}
    doc_data_no = {"customer_consent": False}
    checks = [{"doc_field": "customer_consent", "los_field": None, "method": "presence_only"}]
    
    rec_yes = run_field_checks("kfs", doc_data_yes, {}, checks, "LOAN_001", "test_node")
    assert rec_yes[0]["match_status"] == "MATCH"
    assert rec_yes[0]["confidence"] == 1.0

    rec_no = run_field_checks("kfs", doc_data_no, {}, checks, "LOAN_001", "test_node")
    assert rec_no[0]["match_status"] == "MISMATCH"
    assert rec_no[0]["confidence"] == 0.0


def test_run_field_checks_threshold_90():
    # 9100 vs 10000 -> 91% (>= 90% MATCH)
    checks = [{"doc_field": "loan_amount", "los_field": "loan_amount", "method": "threshold_90"}]
    doc_data = {"loan_amount": 9100.0}
    los_data = {"loan_amount": 10000.0}
    rec = run_field_checks("application_form", doc_data, los_data, checks, "LOAN_001", "test_node")
    assert rec[0]["match_status"] == "MATCH"
    assert rec[0]["confidence"] == 0.91

    # 8500 vs 10000 -> 85% (< 90% MISMATCH)
    doc_data_fail = {"loan_amount": 8500.0}
    rec_fail = run_field_checks("application_form", doc_data_fail, los_data, checks, "LOAN_001", "test_node")
    assert rec_fail[0]["match_status"] == "MISMATCH"
    assert rec_fail[0]["confidence"] == 0.85


def test_compare_bpi_doc_to_doc():
    # Both present, within 10% tolerance (1500 vs 1550 -> 3.2% diff -> MATCH)
    kfs = {"bpi_charge": 1500.0}
    memo = {"bpi_charge": 1550.0}
    rec = compare_bpi_doc_to_doc(kfs, memo, "kfs_sanction")
    assert rec is not None
    assert rec["match_status"] == "MATCH"

    # Both present, exceeding 10% tolerance (1500 vs 2000 -> 25% diff -> MISMATCH)
    memo_bad = {"bpi_charge": 2000.0}
    rec_bad = compare_bpi_doc_to_doc(kfs, memo_bad, "kfs_sanction")
    assert rec_bad is not None
    assert rec_bad["match_status"] == "MISMATCH"

    # One missing -> NOT_FOUND
    rec_missing = compare_bpi_doc_to_doc(kfs, None, "kfs_sanction")
    assert rec_missing is not None
    assert rec_missing["match_status"] == "NOT_FOUND"

    # Neither present -> None (optional gracefully skipped)
    rec_none = compare_bpi_doc_to_doc(None, None, "kfs_sanction")
    assert rec_none is None


def test_aadhaar_helpers():
    assert clean_aadhaar("XXXX XXXX 5552") == "XXXXXXXX5552"
    assert clean_aadhaar("1234-5678-9012") == "123456789012"
    assert clean_aadhaar(None) == ""
    assert clean_aadhaar("") == ""

    assert is_masked_aadhaar("XXXXXXXX5552") is True
    assert is_masked_aadhaar("XXXX-XXXX-5552") is True
    assert is_masked_aadhaar("****-****-5552") is True
    assert is_masked_aadhaar("123456789012") is False
    assert is_masked_aadhaar(None) is False

    assert mask_aadhaar("123456789012") == "XXXXXXXX9012"
    assert mask_aadhaar("123456789012", group_format=True) == "XXXX XXXX 9012"
    assert mask_aadhaar("XXXXXXXX5552") == "XXXXXXXX5552"
    assert mask_aadhaar("123") is None
    assert mask_aadhaar(None) is None


def test_compare_aadhaar():
    # Masked vs Masked with identical last 4
    m, note, conf = compare_aadhaar("XXXXXXXX 5552", "XXXXXXXX5552")
    assert m is True
    assert conf == 1.0

    # Masked vs Full 12 digits with identical last 4
    m, note, conf = compare_aadhaar("XXXXXXXX5552", "999988885552")
    assert m is True
    assert conf == 1.0

    # Exact 12 digits match
    m, note, conf = compare_aadhaar("123456789012", "123456789012")
    assert m is True
    assert conf == 1.0

    # Last 4 mismatch
    m, note, conf = compare_aadhaar("XXXXXXXX5552", "XXXXXXXX1234")
    assert m is False
    assert conf == 0.0

    # Exact 12 digits mismatch
    m, note, conf = compare_aadhaar("123456789012", "123456789099")
    assert m is False
    assert conf == 0.0

    # Missing / None
    m, note, conf = compare_aadhaar(None, "XXXXXXXX5552")
    assert m is False
    assert conf == 0.0


def test_tenure_helpers():
    assert normalize_tenure_months(36) == 36
    assert normalize_tenure_months("36") == 36
    assert normalize_tenure_months("36 months") == 36
    assert normalize_tenure_months("36m") == 36
    assert normalize_tenure_months("3 years") == 36
    assert normalize_tenure_months("2.5 years") == 30
    assert normalize_tenure_months("1 yr") == 12
    assert normalize_tenure_months(None) is None
    assert normalize_tenure_months("") is None
    assert normalize_tenure_months("invalid") is None


def test_compare_tenure():
    m, note, conf = compare_tenure(36, "36")
    assert m is True
    assert conf == 1.0

    m, note, conf = compare_tenure("36 months", "3 years")
    assert m is True
    assert conf == 1.0

    m, note, conf = compare_tenure(36, 48)
    assert m is False
    assert conf == 0.0

    m, note, conf = compare_tenure("invalid", 36)
    assert m is False
    assert conf == 0.0


def test_extract_field_value_with_aliases():
    # Uses centralized FIELD_ALIASES
    assert extract_field_value({"tenure": 36}, "loan_validity") == 36
    assert extract_field_value({"tenure_months": 24}, "loan_validity") == 24
    assert extract_field_value({"loan_amount": 500000}, "loan_amount") == 500000
    assert extract_field_value({"funding_amount": 500000}, "loan_amount") == 500000
    assert extract_field_value({}, "loan_validity") is None
    assert extract_field_value(None, "loan_validity") is None


def test_run_field_checks_aadhaar_and_tenure():
    # Masked Aadhaar check
    doc_aadhaar = {"aadhaar_number": "XXXXXXXX 5552"}
    los_aadhaar = {"aadhaar_number": "XXXXXXXX5552"}
    kyc_checks = [{"doc_field": "aadhaar_number", "los_field": "aadhaar_number", "method": "masked_aadhaar"}]
    rec_aadhaar = run_field_checks("aadhaar", doc_aadhaar, los_aadhaar, kyc_checks, "APPL00343265", "check_kyc")
    assert len(rec_aadhaar) == 1
    assert rec_aadhaar[0]["match_status"] == "MATCH"
    assert rec_aadhaar[0]["match_type"] == "masked_id"

    # Loan validity with alias resolution (doc has loan_validity, LOS has tenure)
    doc_financial = {"loan_validity": 36}
    los_financial = {"tenure": 36}
    fin_checks = [{"doc_field": "loan_validity", "los_field": "loan_validity", "method": "tenure_months"}]
    rec_fin = run_field_checks("kfs", doc_financial, los_financial, fin_checks, "APPL00343265", "check_financial")
    assert len(rec_fin) == 1
    assert rec_fin[0]["match_status"] == "MATCH"
    assert rec_fin[0]["match_type"] == "tenure"

