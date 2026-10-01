from pipeline.nodes.generate_scorecard import _evaluate_checkpoints, generate_scorecard
from pipeline.state import PipelineState

def test_evaluate_checkpoints_matched_fields():
    # Test 1: Empty comparisons -> 0/0
    checkpoints, score = _evaluate_checkpoints([])
    assert len(checkpoints) == 12
    for cp in checkpoints:
        assert cp["matched_fields"] == 0
        assert cp["total_fields"] == 0
        assert cp["match_score"] == 0.0

    # Test 2: With comparison records
    mock_records = [
        {"field": "applicant_name", "match_status": "MATCH", "confidence": 0.95},
        {"field": "dob", "match_status": "MATCH", "confidence": 0.90},
        {"field": "pan_number", "match_status": "MISMATCH", "confidence": 0.80},
    ]
    checkpoints, score = _evaluate_checkpoints(mock_records)
    cp1 = next(cp for cp in checkpoints if cp["id"] == 1)
    assert cp1["total_fields"] == 3
    assert cp1["matched_fields"] == 2
    assert cp1["status"] == "DISCREPANCY"


def test_serializer_checkpoint_counts(tmp_path):
    from app.serializers.case_context import CaseContext
    from app.serializers.checkpoints import build_all_checkpoints

    loan_id = "TEST_001"
    ctx = CaseContext(
        loan_id=loan_id,
        los_data={"loan_id": loan_id, "applicant_name": "Test User", "loan_amount": 500000.0, "tenure": 36},
        docs={
            "application_form": {"loan_amount": 500000.0, "applicant_name": "Test User", "tenure": 36},
            "kfs": {"loan_amount": 500000.0, "tenure": 36},
            "sanction_letter": {"loan_amount": 500000.0, "tenure": 36},
        },
        real_doc_names=["Application_Form.pdf", "Sanction_Letter.pdf", "KFS.pdf"],
        doc_ids=["doc-1", "doc-2", "doc-3"],
        records=[
            {"field": "loan_amount", "match_status": "MATCH", "sources": ["kfs", "los"]},
            {"field": "loan_amount", "match_status": "MATCH", "sources": ["sanction_letter", "los"]},
            {"field": "loan_amount", "match_status": "MATCH", "sources": ["application_form", "los"]},
            {"field": "applicant_name", "match_status": "MATCH", "sources": ["application_form", "los"]},
            {"field": "dob", "match_status": "MATCH", "sources": ["application_form", "los"]},
        ],
        records_by_id={},
        records_by_field={},
        records_by_subnode={},
        status_data={"status": "DONE"},
        scorecard_data={},
        subnode_rollups={},
        loan_amount=500000.0,
        disbursal_amount=450000.0,
        applicant_name="Test User",
        app_id="APP-TEST",
        loan_type="Personal Loan",
        is_bt=False,
        raw_dir=tmp_path / "raw",
        dms_dir=tmp_path / "dms",
        extracted_dir=tmp_path / "extracted",
        extracted_structured_dir=tmp_path / "extracted_structured",
        result_dir=tmp_path / "result",
    )
    cps = build_all_checkpoints(ctx)
    cp1 = next(c for c in cps if c["id"] == 1)  # Loan Amount Consistency
    assert cp1["name"] == "Loan Amount Consistency"
    assert cp1["totalFields"] == 3
    assert cp1["matchedFields"] == 3

    cp3 = next(c for c in cps if c["id"] == 3)  # Application Form Data Check
    assert cp3["name"] == "Application Form Data Check"
    assert cp3["totalFields"] == 3
    assert cp3["matchedFields"] == 3


def test_build_checkpoint_derived_from_comparisons():
    from app.serializers.case_context import build_checkpoint

    mock_comparisons = [
        {"field": "applicant_name", "match_status": "MATCH", "values": ["Rajesh", "Rajesh"]},
        {"field": "dob", "match_status": "MATCH", "values": ["1990-05-15", "1990-05-15"]},
        {"field": "pan_number", "match_status": "MISMATCH", "values": ["ABCDE1234F", "XYZ123"]},
        {"field": "mobile_no", "match_status": "NOT_FOUND", "values": [None, "9876543210"]},
        {"field": "gender", "match_status": "MATCH", "values": ["Male", "Male"]},
    ]

    # Test with status DISCREPANCY and arbitrary fields
    cp = build_checkpoint(
        cp_id=1,
        name="Test Checkpoint",
        status="DISCREPANCY",
        confidence=85.0,
        reason="Discrepancy detected",
        rule="Test rule",
        fields=[{"name": "Dummy", "value": "123", "confidence": 90.0, "sourceDocumentId": "doc-1"}],
        evidence=[],
        comparisons=mock_comparisons,
    )

    assert cp["totalFields"] == 5
    assert cp["total_fields"] == 5
    assert cp["matchedFields"] == 3
    assert cp["matched_fields"] == 3


def test_build_checkpoint_empty_comparisons():
    from app.serializers.case_context import build_checkpoint

    cp_empty = build_checkpoint(
        cp_id=2,
        name="Empty Checkpoint",
        status="INDETERMINATE",
        confidence=0.0,
        reason="No data",
        rule="Test rule",
        fields=[],
        evidence=[],
        comparisons=[],
    )

    assert cp_empty["totalFields"] == 0
    assert cp_empty["total_fields"] == 0
    assert cp_empty["matchedFields"] == 0
    assert cp_empty["matched_fields"] == 0

    cp_none = build_checkpoint(
        cp_id=3,
        name="None Checkpoint",
        status="VERIFIED",
        confidence=100.0,
        reason="Verified",
        rule="Test rule",
        fields=[],
        evidence=[],
        comparisons=None,
    )

    assert cp_none["totalFields"] == 0
    assert cp_none["total_fields"] == 0
    assert cp_none["matchedFields"] == 0
    assert cp_none["matched_fields"] == 0


def test_financial_checkpoints_status_derived_from_all_comparisons(tmp_path):
    from app.serializers.case_context import CaseContext
    from app.serializers.checkpoints.financial import (
        build_loan_amount_checkpoint,
        build_loan_validity_checkpoint,
        build_kfs_checkpoint,
        build_sanction_letter_checkpoint,
    )

    loan_id = "TEST_FIN_001"
    
    # 1. NOT_FOUND in comparisons -> INDETERMINATE
    ctx_not_found = CaseContext(
        loan_id=loan_id,
        los_data={"loan_id": loan_id, "loan_amount": 500000.0, "tenure": 36, "irr_percent": 15.0, "emi": 17000.0},
        docs={
            "application_form": {"loan_amount": 500000.0, "loan_validity": 36},
            "kfs": {"loan_amount": 500000.0, "loan_validity": 36, "irr_percent": 15.0, "emi": 17000.0, "customer_consent": True},
            "sanction_letter": {"loan_amount": 500000.0, "tenure_months": 36, "interest_rate": 15.0, "emi": 17000.0},
        },
        real_doc_names=["Application_Form.pdf", "Sanction_Letter.pdf", "KFS.pdf"],
        doc_ids=["doc-1", "doc-2", "doc-3"],
        records=[
            {"field": "loan_amount", "match_status": "MATCH", "sources": ["kfs", "los"], "check_id": "chk_check_financial_kfs_loan_amount_vs_los"},
            {"field": "loan_amount", "match_status": "NOT_FOUND", "sources": ["sanction_letter", "los"], "check_id": "chk_check_financial_sanction_letter_loan_amount_vs_los"},
            {"field": "tenure", "match_status": "NOT_FOUND", "sources": ["application_form", "los"], "check_id": "chk_check_financial_loan_validity_application_form_vs_los"},
            {"field": "irr_percent", "match_status": "NOT_FOUND", "sources": ["kfs", "los"], "check_id": "chk_check_financial_kfs_irr_percent_vs_los"},
            {"field": "interest_rate", "match_status": "NOT_FOUND", "sources": ["sanction_letter", "los"], "check_id": "chk_check_financial_sanction_letter_roi_vs_los"},
        ],
        records_by_id={},
        records_by_field={},
        records_by_subnode={},
        status_data={"status": "DONE"},
        scorecard_data={},
        subnode_rollups={},
        loan_amount=500000.0,
        disbursal_amount=450000.0,
        applicant_name="Test User",
        app_id="APP-TEST",
        loan_type="Personal Loan",
        is_bt=False,
        raw_dir=tmp_path / "raw",
        dms_dir=tmp_path / "dms",
        extracted_dir=tmp_path / "extracted",
        extracted_structured_dir=tmp_path / "extracted_structured",
        result_dir=tmp_path / "result",
    )

    cp1_nf = build_loan_amount_checkpoint(ctx_not_found)
    assert cp1_nf["status"] == "INDETERMINATE"

    cp2_nf = build_loan_validity_checkpoint(ctx_not_found)
    assert cp2_nf["status"] == "INDETERMINATE"

    cp7_nf = build_kfs_checkpoint(ctx_not_found)
    assert cp7_nf["status"] == "INDETERMINATE"

    cp8_nf = build_sanction_letter_checkpoint(ctx_not_found)
    assert cp8_nf["status"] == "INDETERMINATE"

    # 2. MISMATCH in comparisons -> DISCREPANCY
    ctx_mismatch = CaseContext(
        loan_id=loan_id,
        los_data={"loan_id": loan_id, "loan_amount": 500000.0, "tenure": 36, "irr_percent": 15.0, "emi": 17000.0},
        docs={
            "application_form": {"loan_amount": 500000.0, "loan_validity": 36},
            "kfs": {"loan_amount": 500000.0, "loan_validity": 36, "irr_percent": 15.0, "emi": 17000.0, "customer_consent": True},
            "sanction_letter": {"loan_amount": 500000.0, "tenure_months": 36, "interest_rate": 15.0, "emi": 17000.0},
        },
        real_doc_names=["Application_Form.pdf", "Sanction_Letter.pdf", "KFS.pdf"],
        doc_ids=["doc-1", "doc-2", "doc-3"],
        records=[
            {"field": "loan_amount", "match_status": "MISMATCH", "values": ["500000", "600000"], "sources": ["kfs", "los"], "check_id": "chk_check_financial_kfs_loan_amount_vs_los"},
            {"field": "tenure", "match_status": "MISMATCH", "values": ["36", "48"], "sources": ["application_form", "los"], "check_id": "chk_check_financial_loan_validity_application_form_vs_los"},
            {"field": "irr_percent", "match_status": "MISMATCH", "values": ["15.0%", "17.0%"], "sources": ["kfs", "los"], "check_id": "chk_check_financial_kfs_irr_percent_vs_los"},
            {"field": "interest_rate", "match_status": "MISMATCH", "values": ["15.0%", "17.0%"], "sources": ["sanction_letter", "los"], "check_id": "chk_check_financial_sanction_letter_roi_vs_los"},
        ],
        records_by_id={},
        records_by_field={},
        records_by_subnode={},
        status_data={"status": "DONE"},
        scorecard_data={},
        subnode_rollups={},
        loan_amount=500000.0,
        disbursal_amount=450000.0,
        applicant_name="Test User",
        app_id="APP-TEST",
        loan_type="Personal Loan",
        is_bt=False,
        raw_dir=tmp_path / "raw",
        dms_dir=tmp_path / "dms",
        extracted_dir=tmp_path / "extracted",
        extracted_structured_dir=tmp_path / "extracted_structured",
        result_dir=tmp_path / "result",
    )

    cp1_mis = build_loan_amount_checkpoint(ctx_mismatch)
    assert cp1_mis["status"] == "DISCREPANCY"

    cp2_mis = build_loan_validity_checkpoint(ctx_mismatch)
    assert cp2_mis["status"] == "DISCREPANCY"

    cp7_mis = build_kfs_checkpoint(ctx_mismatch)
    assert cp7_mis["status"] == "DISCREPANCY"

    cp8_mis = build_sanction_letter_checkpoint(ctx_mismatch)
    assert cp8_mis["status"] == "DISCREPANCY"


