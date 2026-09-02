from pipeline.nodes.node3b_kfs_sanction import node3b_kfs_sanction
from pipeline.state import PipelineState


def test_node3b_clean_match(mock_state_001: PipelineState):
    result = node3b_kfs_sanction(mock_state_001)
    # LOAN_001 has mock PDF which has no cert, but signature is handled or mocked;
    # checks: selfie, otp, kfs funding, sanction funding, aadhaar
    records = result["records"]
    assert any(r["field"] == "selfie_live_photo" and r["match_status"] == "MATCH" for r in records)
    assert any(r["field"] == "loan_agreement_otp_consent" and r["match_status"] == "MATCH" for r in records)
    assert any(r["field"] == "aadhaar_xml" and r["match_status"] == "MATCH" for r in records)
    assert any(r["field"] == "funding_amount" and r["match_status"] == "MATCH" for r in records)


def test_node3b_aadhaar_missing_hard_gate(mock_state_001: PipelineState):
    state = dict(mock_state_001)
    state["loan_id"] = "LOAN_002"
    state["dms_status"] = {"aadhaar_xml": {"exists": False}}
    state["extracted_data"]["aadhar_xml"] = None

    result = node3b_kfs_sanction(state)
    assert result["rollup"] == "Discrepancy"

    aadhaar_rec = next(r for r in result["records"] if r["field"] == "aadhaar_xml")
    assert aadhaar_rec["match_status"] == "MISMATCH"
    assert "mandatory hard gate failed" in (aadhaar_rec["notes"] or "")


def test_node3b_face_similarity_mismatch(mock_state_001: PipelineState):
    state = dict(mock_state_001)
    # Opposite vectors yield cosine similarity -1.0 (< 0.75 MISMATCH)
    state["face_embeddings"] = {
        "selfie_vector": [1.0] * 128,
        "application_form_photo_vector": [-1.0] * 128,
    }
    result = node3b_kfs_sanction(state)
    face_rec = next(r for r in result["records"] if r["field"] == "selfie_live_photo")
    assert face_rec["match_status"] == "MISMATCH"
    assert result["rollup"] == "Discrepancy"


def test_node3b_otp_consent_false(mock_state_001: PipelineState):
    state = dict(mock_state_001)
    state["otp_audit"] = {"otp_verified": False}
    result = node3b_kfs_sanction(state)
    otp_rec = next(r for r in result["records"] if r["field"] == "loan_agreement_otp_consent")
    assert otp_rec["match_status"] == "MISMATCH"
    assert result["rollup"] == "Discrepancy"
