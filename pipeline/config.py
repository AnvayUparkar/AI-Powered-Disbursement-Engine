"""Pipeline configuration shim — forwards to config package for backward compatibility."""
from config import *
from config.pipeline_checks import (
    KYC_FIELD_CHECKS as NODE3A_FIELD_CHECKS,
    FINANCIAL_FIELD_CHECKS as NODE3B_FIELD_CHECKS,
    LOAN_APP_FIELD_CHECKS as NODE3C_FIELD_CHECKS,
)

CHECKER_MIN_CONFIDENCE_THRESHOLD = 0.70
MAX_CHECKER_RETRIES = 2
CHECKER_REQUIRED_DOCUMENTS = ["application_form", "pan_card", "loan_agreement"]
CHECKER_REQUIRED_LOS_FIELDS = ["loan_id", "applicant_name", "loan_amount"]
