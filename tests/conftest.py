import pytest

from pipeline.state import PipelineState


@pytest.fixture
def mock_state_001() -> PipelineState:
    return {
        "loan_id": "LOAN_001",
        "los_data": {
            "loan_id": "LOAN_001",
            "applicant_name": "Rajesh Sharma",
            "funding_amount": 500000.0,
            "tenure_months": 24,
            "application_id": "APP_001",
            "pan": "ABCDE1234F",
        },
        "raw_doc_paths": {},
        "extracted_data": {
            "application_form": {
                "applicant_name": "Rajesh Sharma",
                "loan_amount": "500000",
                "tenure_months": 24,
                "pan_number": "ABCDE1234F",
                "address_text": "123 MG Road, Bengaluru, Karnataka, 560001",
                "application_id": "APP_001",
            },
            "loan_agreement": {"loan_amount": 500000.0, "tenure_months": 24},
            "kfs": {"loan_amount": 500000.0, "funding_amount": 500000.0, "broken_period_interest": 1500.0},
            "sanction_letter": {"loan_amount": 500000.0, "funding_amount": 500000.0, "tenure_months": 24, "broken_period_interest": 1500.0},
            "kyc_pan": {"pan_number": "ABCDE1234F", "name": "Rajesh Sharma"},
            "kyc_address_proof": {"address_text": "123 MG Road, Bengaluru, Karnataka, 560001"},
            "disbursal_memo": {
                "application_id": "APP_001",
                "closure_id": "CLOSURE_8877",
                "disbursal_amount": 480000.0,
            },
        },
        "face_embeddings": {
            "selfie_vector": [0.1] * 128,
            "application_form_photo_vector": [0.101] * 128,
        },
        "dms_status": {"aadhaar_xml": {"exists": True}},
        "otp_audit": {"otp_verified": True},
        "comparison_results": [],
        "subnode_rollups": {},
        "compiled_report": {},
        "scorecard": {},
        "errors": [],
        "node_history": [],
    }
