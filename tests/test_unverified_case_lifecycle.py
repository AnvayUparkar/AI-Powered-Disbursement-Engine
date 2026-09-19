"""Real integration tests for case lifecycle invariants without mocks or monkeypatches.
Tests:
1. Pure domain logic: Asymmetric and symmetrical missing invariants in resolve_checkpoint_validation.
2. Real storage serialization: Case with uploaded documents but un-run verification renders INDETERMINATE with hasLosData=False.
3. Live API endpoint: POST /api/cases/{case_id}/run-ocr on real disk assets without running downstream checkers.
"""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.serializers.case_context import resolve_checkpoint_validation
from app.serializers.case_serializer import serialize_case
from config import LOS_LOANS_DIR, S3_EXTRACTED_DIR, S3_RAW_DIR, S3_RESULT_DIR
from pipeline.storage import delete_loan_data, write_json


@pytest.fixture
def client():
    return TestClient(app)


def test_asymmetric_missing_invariant_pure_logic():
    """Validates that comparing missing values against concrete values never yields MATCH."""
    # 1. Asymmetric: LOS N/A vs Sanction ₹32,000 in INDETERMINATE status
    val1 = resolve_checkpoint_validation(
        status="INDETERMINATE",
        default_left="N/A",
        default_right="₹32,000",
        default_left_source="los",
        default_right_source="sanction_letter",
    )
    assert val1["result"] == "INCONCLUSIVE"
    assert val1["left"] == "N/A"
    assert val1["right"] == "₹32,000"

    # 2. Asymmetric: Sanction ₹32,000 vs Missing in DISCREPANCY status
    val2 = resolve_checkpoint_validation(
        status="DISCREPANCY",
        default_left="₹32,000",
        default_right="Missing",
        default_left_source="sanction_letter",
        default_right_source="los",
    )
    assert val2["result"] == "MISMATCH"

    # 3. Symmetrical: N/A vs N/A in INDETERMINATE status
    val3 = resolve_checkpoint_validation(
        status="INDETERMINATE",
        default_left="N/A",
        default_right="N/A",
    )
    assert val3["result"] == "INCONCLUSIVE"

    # 4. Symmetrical: Not Available vs Not Applicable in NOT_APPLICABLE status
    val4 = resolve_checkpoint_validation(
        status="NOT_APPLICABLE",
        default_left="Not Available",
        default_right="Not Applicable",
    )
    assert val4["result"] == "MATCH"

    # 5. Equal concrete values in VERIFIED status
    val5 = resolve_checkpoint_validation(
        status="VERIFIED",
        default_left="₹500,000",
        default_right="₹500,000",
    )
    assert val5["result"] == "MATCH"


def test_real_unverified_case_serialization_on_disk():
    """Creates real files on disk for a new case and verifies that serialize_case reflects genuine state."""
    case_id = "REAL_UNVERIFIED_TEST_CASE"
    # Ensure clean starting state
    delete_loan_data(case_id)

    try:
        # Create an extracted document file (simulating OCR output present, but NO comparison results and NO LOS)
        extracted_doc_path = S3_EXTRACTED_DIR / case_id / "sanction_letter.json"
        write_json(extracted_doc_path, {
            "loan_amount": 32000.0,
            "applicant_name": "Ritesh Test",
            "loan_validity": "36 Months",
        })

        # Serialize real case from disk
        serialized = serialize_case(case_id)

        assert serialized["id"] == case_id
        assert serialized["hasLosData"] is False
        assert serialized["status"] in ("INDETERMINATE", "PROCESSING")
        assert serialized["dgclScore"] == 0.0

        # Checkpoint 1 (Loan Amount) must be INDETERMINATE and INCONCLUSIVE (not MATCH)
        cp1 = next(cp for cp in serialized["checkpoints"] if cp["id"] == 1)
        assert cp1["status"] == "INDETERMINATE"
        assert cp1["validation"] is not None
        assert cp1["validation"]["result"] == "INCONCLUSIVE"
        assert cp1["validation"]["left"] == "N/A"
        assert "32,000" in cp1["validation"]["right"]

    finally:
        # Clean up created artifacts
        delete_loan_data(case_id)


def test_real_run_ocr_endpoint_execution(client):
    """Executes the live POST /api/cases/{case_id}/run-ocr endpoint on real disk structure."""
    case_id = "REAL_OCR_ENDPOINT_TEST"
    delete_loan_data(case_id)

    try:
        # Place a dummy text / raw file in S3 RAW DIR
        raw_file = S3_RAW_DIR / case_id / "sanction_letter.json"
        write_json(raw_file, {
            "loan_amount": 75000.0,
            "applicant_name": "OCR Test User",
        })

        response = client.post(f"/api/cases/{case_id}/run-ocr")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["case"]["id"] == case_id
        assert data["case"]["hasLosData"] is False

    finally:
        delete_loan_data(case_id)
