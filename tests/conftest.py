import os
import tempfile

# Keep the auth database out of the repo's poc_data during test runs (must precede config imports).
os.environ.setdefault("AUTH_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="dgcl_test_auth_"), "auth.db"))

import pytest

from config.paths import POC_DATA_DIR
from config.tenant import set_tenant, reset_tenant
from pipeline.state import PipelineState

DEFAULT_TEST_TENANT = "t_test"
_TENANT_TIERS = (
    "los", "dms", "s3_los", "s3_raw", "s3_extracted", "s3_extracted_structured", "s3_result",
)


@pytest.fixture(scope="session")
def _shared_tenants_root(tmp_path_factory):
    """Tenant root whose default test tenant maps onto the existing poc_data tiers (legacy fixtures)."""
    root = tmp_path_factory.mktemp("tenants")
    tenant_dir = root / DEFAULT_TEST_TENANT
    tenant_dir.mkdir()
    for tier in _TENANT_TIERS:
        POC_DATA_DIR.joinpath(tier).mkdir(parents=True, exist_ok=True)
        os.symlink(POC_DATA_DIR / tier, tenant_dir / tier)
    return root


@pytest.fixture(autouse=True)
def _default_tenant(_shared_tenants_root, monkeypatch, request):
    """Run every test as a default tenant unless it opts out with @pytest.mark.no_default_tenant."""
    monkeypatch.setattr("config.tenant.TENANTS_ROOT", _shared_tenants_root)
    if "no_default_tenant" in request.keywords:
        yield
        return
    token = set_tenant(DEFAULT_TEST_TENANT)
    try:
        yield
    finally:
        reset_tenant(token)


@pytest.fixture(autouse=True)
def _bypass_auth(request):
    """API tests act as the default tenant; @pytest.mark.real_auth exercises the real login flow."""
    if "real_auth" in request.keywords:
        yield
        return
    from app.auth import require_tenant
    from app.main import app as api_app

    async def _as_default_tenant():
        set_tenant(DEFAULT_TEST_TENANT)
        return DEFAULT_TEST_TENANT

    from idp.main import app as idp_app

    apps = (api_app, idp_app)
    for a in apps:
        a.dependency_overrides[require_tenant] = _as_default_tenant
    try:
        yield
    finally:
        for a in apps:
            a.dependency_overrides.pop(require_tenant, None)


@pytest.fixture
def mock_state_001() -> PipelineState:
    return {
        "loan_id": "LOAN_001",
        "los_data": {
            "loan_id": "LOAN_001",
            "applicant_name": "Rajesh Sharma",
            "loan_amount": 500000.0,
            "funding_amount": 500000.0,
            "applicant_mobile_no": "9876543210",
            "applicant_dob": "1990-05-15",
            "applicant_pan_number": "ABCDE1234F",
            "fathers_name": "Suresh Sharma",
            "applicant_bank_account_no": "987654321012",
            "loan_type": "Personal Loan",
            "loan_validity": "24 months",
            "current_address": "123 MG Road, Bengaluru, Karnataka, 560001",
            "permanent_address": "123 MG Road, Bengaluru, Karnataka, 560001",
            "aadhaar_no": "123456789012",
            "application_date": "2024-01-10",
            "bank_account_type": "Savings",
            "applicant_gender": "Male",
            "login_date": "2024-01-11",
            "disbursement_date": "2024-01-15",
            "tenure_months": 24,
            "application_id": "LOAN_001",
            "pan": "ABCDE1234F",
            "irr_percent": 12.5,
            "emi": 23600.0,
        },
        "raw_doc_paths": {},
        "extracted_data": {
            "aadhaar": {
                "applicant_name": "Rajesh Sharma",
                "address": "123 MG Road, Bengaluru, Karnataka, 560001",
                "aadhaar_number": "123456789012",
                "mobile_no": "9876543210",
                "dob": "1990-05-15",
            },
            "pan": {
                "applicant_name": "Rajesh Sharma",
                "fathers_name": "Suresh Sharma",
                "pan_number": "ABCDE1234F",
                "dob": "1990-05-15",
            },
            "application_form": {
                "applicant_name": "Rajesh Sharma",
                "fathers_name": "Suresh Sharma",
                "dob": "1990-05-15",
                "mobile_no": "9876543210",
                "gender": "Male",
                "pan_number": "ABCDE1234F",
                "loan_amount": "500000",
                "loan_validity": "24 months",
                "account_no": "987654321012",
                "type_of_account": "Savings",
                "loan_type": "Personal Loan",
                "current_address": "123 MG Road, Bengaluru, Karnataka, 560001",
                "application_date": "2024-01-10",
                "application_no": "LOAN_001",
                "tenure_months": 24,
                "address_text": "123 MG Road, Bengaluru, Karnataka, 560001",
                "application_id": "LOAN_001",
            },
            "account_statement": {
                "applicant_name": "Rajesh Sharma",
                "pan_number": "ABCDE1234F",
                "mobile_no": "9876543210",
                "account_no": "987654321012",
            },
            "loan_agreement": {"loan_amount": 500000.0, "tenure_months": 24},
            "kfs": {
                "application_no": "LOAN_001",
                "loan_amount": 500000.0,
                "funding_amount": 500000.0,
                "loan_validity": "24 months",
                "loan_type": "Personal Loan",
                "customer_consent": True,
                "bpi_charge": 1500.0,
                "broken_period_interest": 1500.0,
                "irr_percent": 12.5,
                "emi": 23600.0,
            },
            "sanction_letter": {
                "application_no": "LOAN_001",
                "applicant_name": "Rajesh Sharma",
                "loan_amount": 500000.0,
                "funding_amount": 500000.0,
                "tenure_months": 24,
                "broken_period_interest": 1500.0,
                "irr_percent": 12.5,
                "emi": 23600.0,
            },
            "kyc_pan": {"pan_number": "ABCDE1234F", "name": "Rajesh Sharma"},
            "kyc_address_proof": {"address_text": "123 MG Road, Bengaluru, Karnataka, 560001"},
            "disbursal_memo": {
                "loan_no": "LOAN_001",
                "loan_amount": 490000.0,
                "bpi_charge": 1500.0,
                "application_id": "LOAN_001",
                "closure_id": "CLOSURE_8877",
                "disbursal_amount": 490000.0,
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
        "retry_count": 0,
        "checker_result": {},
        "errors": [],
        "node_history": [],
    }

