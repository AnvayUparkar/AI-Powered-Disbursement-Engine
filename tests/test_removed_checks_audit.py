"""Unit tests verifying the removal of application form current address check,
and application date checks with LOS across application form, KFS, and sanction letter.
"""
from pathlib import Path
import pytest

from config.pipeline_checks import FINANCIAL_FIELD_CHECKS, LOAN_APP_FIELD_CHECKS, CHECKPOINTS_SPEC
from pipeline.nodes.check_financial import check_financial
from pipeline.nodes.check_loan_app import check_loan_app as check_loan_application
from pipeline.state import PipelineState
from app.serializers.case_serializer import CaseContext
from app.serializers.checkpoints.identity_kyc import build_application_form_checkpoint


def test_config_checks_do_not_contain_removed_fields():
    """Verify configuration dictionaries no longer define the removed checks."""
    # 1. current_address removed from application_form in FINANCIAL_FIELD_CHECKS
    app_fin_fields = [c["doc_field"] for c in FINANCIAL_FIELD_CHECKS.get("application_form", [])]
    assert "current_address" not in app_fin_fields

    # 2. application_date removed from application_form in LOAN_APP_FIELD_CHECKS
    app_loan_fields = [c["doc_field"] for c in LOAN_APP_FIELD_CHECKS.get("application_form", [])]
    assert "application_date" not in app_loan_fields

    # 3. application_date removed from kfs in LOAN_APP_FIELD_CHECKS
    kfs_loan_fields = [c["doc_field"] for c in LOAN_APP_FIELD_CHECKS.get("kfs", [])]
    assert "application_date" not in kfs_loan_fields

    # 4. application_date removed from sanction_letter in LOAN_APP_FIELD_CHECKS
    sanction_loan_fields = [c["doc_field"] for c in LOAN_APP_FIELD_CHECKS.get("sanction_letter", [])]
    assert "application_date" not in sanction_loan_fields

    # Checkpoint 12 fields spec should only require application_no
    cp12 = next(cp for cp in CHECKPOINTS_SPEC if cp["id"] == 12)
    assert "application_date" not in cp12["fields"]
    assert "application_no" in cp12["fields"]


def test_check_financial_ignores_application_form_current_address():
    """check_financial must not compare or emit current_address records for application_form even if present and conflicting."""
    state: PipelineState = {
        "loan_id": "TEST_ADDR_REMOVED",
        "los_data": {
            "loan_id": "TEST_ADDR_REMOVED",
            "loan_amount": 500000.0,
            "tenure": 24,
            "applicant_bank_account_no": "1234567890",
            "bank_account_type": "Savings",
            "loan_type": "Personal",
            "current_address": "123 MG Road, Bangalore",
        },
        "extracted_data": {
            "application_form": {
                "loan_amount": 500000.0,
                "loan_validity": 24,
                "account_no": "1234567890",
                "type_of_account": "Savings",
                "loan_type": "Personal",
                "current_address": "Completely Different Address, Mumbai",
            }
        },
        "comparison_results": [],
        "subnode_rollups": {},
    }

    res = check_financial(state)
    records = res["records"]

    # There must be zero records comparing application_form with current_address
    app_addr_records = [
        r for r in records
        if r.get("field") == "current_address" and "application_form" in (r.get("sources") or [])
    ]
    assert len(app_addr_records) == 0

    # Other checks must still execute and match
    assert any(r.get("field") == "loan_amount" and r.get("match_status") == "MATCH" for r in records)


def test_check_loan_application_ignores_date_conflicts():
    """check_loan_application must not compare or emit application_date records for application_form, kfs, or sanction_letter."""
    state: PipelineState = {
        "loan_id": "TEST_DATES_REMOVED",
        "los_data": {
            "loan_id": "TEST_DATES_REMOVED",
            "application_date": "2024-01-10",
        },
        "extracted_data": {
            "application_form": {
                "application_no": "TEST_DATES_REMOVED",
                "application_date": "1999-12-31",  # Deliberate conflict
            },
            "kfs": {
                "application_no": "TEST_DATES_REMOVED",
                "application_date": "2020-05-01",  # Deliberate conflict
            },
            "sanction_letter": {
                "application_no": "TEST_DATES_REMOVED",
                "application_date": "2022-08-15",  # Deliberate conflict
            },
            "disbursal_memo": {
                "loan_no": "TEST_DATES_REMOVED",
            },
        },
        "comparison_results": [],
        "subnode_rollups": {},
    }

    res = check_loan_application(state)
    records = res["records"]

    # No application_date checks anywhere
    date_records = [r for r in records if r.get("field") == "application_date"]
    assert len(date_records) == 0

    # All application_no checks should MATCH
    app_no_matches = [r for r in records if r.get("field") == "application_no" and r.get("match_status") == "MATCH"]
    assert len(app_no_matches) == 3
    assert res["rollup"] == "Verified"


def test_application_form_checkpoint_serializer_ignores_date_and_address_mismatch(tmp_path: Path):
    """CP 3 serializer must not downgrade to DISCREPANCY when application_date or address differ between application form and LOS."""
    docs = {
        "application_form": {
            "applicant_name": "Prakash Khatri",
            "application_no": "APPL00343265",
            "mobile_no": "9079533555",
            "pan_number": "AOOPK6924P",
            "fathers_name": "Gyan Chand Khatri",
            "dob": "22/06/1976",
            "gender": "Male",
            "loan_amount": 1000000.0,
            "loan_validity": 36,
            "account_no": "50200064998229",
            "type_of_account": "Savings",
            "loan_type": "Business Loan",
            # Conflicting date and address:
            "application_date": "06/08/2026",
            "current_address": "Flat 101, New Town, Pune",
        }
    }
    los_data = {
        "loan_id": "APPL00343265",
        "applicant_name": "Prakash Khatri",
        "applicant_mobile_no": "9079533555",
        "applicant_pan_number": "AOOPK6924P",
        "fathers_name": "Gyan Chand Khatri",
        "applicant_dob": "22/06/1976",
        "applicant_gender": "Male",
        "loan_amount": 1000000.0,
        "tenure": 36,
        "applicant_bank_account_no": "50200064998229",
        "bank_account_type": "Savings",
        "loan_type": "Business Loan",
        # LOS values differ:
        "application_date": "22/08/2026",
        "current_address": "30/105, Sindhi Colony, Jaipur",
    }

    from tests.test_case_serializer_prod import make_test_context

    ctx = make_test_context(
        tmp_path,
        loan_id="APPL00343265",
        applicant_name="Prakash Khatri",
        loan_amount=1000000.0,
        los_data=los_data,
        docs=docs,
    )

    cp3 = build_application_form_checkpoint(ctx)

    # Since all non-date, non-address fields match, CP 3 must remain VERIFIED with 100% match score
    assert cp3["status"] == "VERIFIED"
    assert cp3["matchScore"] == 100.0
    assert "Discrepancy detected in Application Date" not in cp3["reason"]
    assert "Discrepancy detected in Address" not in cp3["reason"]
