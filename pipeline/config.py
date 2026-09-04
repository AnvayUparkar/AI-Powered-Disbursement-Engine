"""Pipeline Configuration — Domain verification thresholds, matching algorithms, and policy rules."""
import os

# Verification Algorithms & Fuzzy Match Thresholds
NAME_MATCH_ALGO = "jaro_winkler"
ADDRESS_MATCH_ALGO = "tfidf_cosine"

FUZZY_MATCH_BAND = 0.92          # >= 0.92: auto MATCH
FUZZY_PARTIAL_LOWER = 0.75       # 0.75 - 0.92: PARTIAL -> Gemini LLM adjudication, < 0.75: MISMATCH

FACE_MATCH_BAND = 0.90           # >= 0.90: auto MATCH
FACE_REVIEW_LOWER = 0.75         # 0.75 - 0.90: PARTIAL/REVIEW, < 0.75: MISMATCH

# Loan Amount & Policy Thresholds
LOAN_AMOUNT_THRESHOLD_PCT = 0.90
DISBURSAL_MEMO_THRESHOLD_PCT = 0.90
BROKEN_PERIOD_INTEREST_TOLERANCE_PCT = 0.10
FUNDING_AMOUNT_SOURCE_FIELD = "funding_amount"

# Pipeline Checker Node & Confidence Retries
CHECKER_MIN_CONFIDENCE_THRESHOLD = float(os.getenv("CHECKER_MIN_CONFIDENCE_THRESHOLD", "0.70"))
MAX_CHECKER_RETRIES = int(os.getenv("MAX_CHECKER_RETRIES", "2"))
CHECKER_REQUIRED_DOCUMENTS = [
    "application_form",
    "pan_card",
    "loan_agreement",
]
CHECKER_REQUIRED_LOS_FIELDS = [
    "loan_id",
    "applicant_name",
    "loan_amount",
]

# ── Neo LOS DB column -> canonical field name ──────────────────────────────
NEO_LOS_FIELD_MAP: dict[str, str] = {
    "loan_id": "loan_id",
    "applicant_name": "applicant_name",
    "loan_amount": "loan_amount",
    "applicant_mobile_no": "applicant_mobile_no",
    "applicant_dob": "applicant_dob",
    "applicant_pan_number": "applicant_pan_number",
    "fathers_name": "fathers_name",
    "applicant_bank_account_no": "applicant_bank_account_no",
    "loan_type": "loan_type",
    "loan_validity": "loan_validity",
    "current_address": "current_address",
    "permanent_address": "permanent_address",
    "aadhaar_no": "aadhaar_no",
    "application_date": "application_date",
    "bank_account_type": "bank_account_type",
    "applicant_gender": "applicant_gender",
    "login_date": "login_date",
    "disbursement_date": "disbursement_date",
}

# ── Per-subnode field check configurations ─────────────────────────────────
# Structure: {doc_type: [{"doc_field": ..., "los_field": ..., "method": ..., "optional": bool}]}
NODE3A_FIELD_CHECKS: dict[str, list[dict]] = {
    "aadhaar": [
        {"doc_field": "applicant_name", "los_field": "applicant_name", "method": "jaro_winkler", "aliases": ["name"]},
        {"doc_field": "address", "los_field": "current_address", "method": "tfidf_cosine", "aliases": ["current_address", "address_text"]},
        {"doc_field": "aadhaar_number", "los_field": "aadhaar_no", "method": "exact_id", "aliases": ["aadhaar", "aadhaar_no"]},
        {"doc_field": "mobile_no", "los_field": "applicant_mobile_no", "method": "exact_string", "aliases": ["applicant_mobile_no", "mobile"]},
        {"doc_field": "dob", "los_field": "applicant_dob", "method": "exact_date", "aliases": ["applicant_dob", "date_of_birth"]},
    ],
    "pan": [
        {"doc_field": "applicant_name", "los_field": "applicant_name", "method": "jaro_winkler", "aliases": ["name"]},
        {"doc_field": "fathers_name", "los_field": "fathers_name", "method": "jaro_winkler", "aliases": ["father_name"]},
        {"doc_field": "pan_number", "los_field": "applicant_pan_number", "method": "exact_id", "aliases": ["pan", "applicant_pan_number"]},
        {"doc_field": "dob", "los_field": "applicant_dob", "method": "exact_date", "aliases": ["applicant_dob", "date_of_birth"]},
    ],
    "application_form": [
        {"doc_field": "applicant_name", "los_field": "applicant_name", "method": "jaro_winkler", "aliases": ["name"]},
        {"doc_field": "fathers_name", "los_field": "fathers_name", "method": "jaro_winkler", "aliases": ["father_name"]},
        {"doc_field": "dob", "los_field": "applicant_dob", "method": "exact_date", "aliases": ["applicant_dob", "date_of_birth"]},
        {"doc_field": "mobile_no", "los_field": "applicant_mobile_no", "method": "exact_string", "aliases": ["applicant_mobile_no", "mobile"]},
        {"doc_field": "gender", "los_field": "applicant_gender", "method": "exact_string_ci", "aliases": ["applicant_gender"]},
        {"doc_field": "pan_number", "los_field": "applicant_pan_number", "method": "exact_id", "aliases": ["pan", "applicant_pan_number"]},
    ],
    "account_statement": [
        {"doc_field": "applicant_name", "los_field": "applicant_name", "method": "jaro_winkler", "aliases": ["name", "account_holder_name"]},
        {"doc_field": "pan_number", "los_field": "applicant_pan_number", "method": "exact_id", "aliases": ["pan", "applicant_pan_number"]},
        {"doc_field": "mobile_no", "los_field": "applicant_mobile_no", "method": "exact_string", "aliases": ["applicant_mobile_no", "mobile"]},
        {"doc_field": "account_no", "los_field": "applicant_bank_account_no", "method": "exact_id", "aliases": ["account_number", "bank_account_no", "applicant_bank_account_no"]},
    ],
}

NODE3B_FIELD_CHECKS: dict[str, list[dict]] = {
    "application_form": [
        {"doc_field": "loan_amount", "los_field": "loan_amount", "method": "threshold_90", "aliases": ["funding_amount", "amount"]},
        {"doc_field": "loan_validity", "los_field": "loan_validity", "method": "exact_string_ci", "aliases": ["tenure", "tenure_months"]},
        {"doc_field": "account_no", "los_field": "applicant_bank_account_no", "method": "exact_id", "aliases": ["account_number", "applicant_bank_account_no", "bank_account_no"]},
        {"doc_field": "type_of_account", "los_field": "bank_account_type", "method": "exact_string_ci", "aliases": ["bank_account_type", "account_type"]},
        {"doc_field": "loan_type", "los_field": "loan_type", "method": "exact_string_ci", "aliases": []},
        {"doc_field": "current_address", "los_field": "current_address", "method": "tfidf_cosine", "aliases": ["address", "address_text"]},
    ],
    "kfs": [
        {"doc_field": "loan_amount", "los_field": "loan_amount", "method": "threshold_90", "aliases": ["funding_amount", "amount"]},
        {"doc_field": "loan_validity", "los_field": "loan_validity", "method": "exact_string_ci", "aliases": ["tenure", "tenure_months"]},
        {"doc_field": "loan_type", "los_field": "loan_type", "method": "exact_string_ci", "aliases": []},
        {"doc_field": "loan_account_no", "los_field": "loan_id", "method": "exact_id", "aliases": ["loan_no", "loan_id"]},
        {"doc_field": "customer_consent", "los_field": None, "method": "presence_only", "aliases": ["consent", "is_consented"]},
    ],
    "disbursal_memo": [
        {"doc_field": "loan_no", "los_field": "loan_id", "method": "exact_id", "aliases": ["loan_id", "loan_number", "application_id"]},
        {"doc_field": "loan_amount", "los_field": "loan_amount", "method": "threshold_90", "aliases": ["disbursal_amount", "amount"]},
    ],
    "sanction_letter": [
        {"doc_field": "applicant_name", "los_field": "applicant_name", "method": "jaro_winkler", "aliases": ["name"]},
        {"doc_field": "loan_amount", "los_field": "loan_amount", "method": "threshold_90", "aliases": ["funding_amount", "amount", "sanctioned_amount"]},
    ],
}

NODE3C_FIELD_CHECKS: dict[str, list[dict]] = {
    "application_form": [
        {"doc_field": "application_date", "los_field": "application_date", "method": "exact_date", "aliases": ["date_of_application", "app_date"]},
        {"doc_field": "application_no", "los_field": "loan_id", "method": "exact_id", "aliases": ["application_id", "loan_id"]},
        {"doc_field": "login_date", "los_field": "login_date", "method": "exact_date", "aliases": []},
        {"doc_field": "disbursement_date", "los_field": "disbursement_date", "method": "exact_date", "aliases": []},
    ],
    "disbursal_memo": [
        {"doc_field": "loan_no", "los_field": "loan_id", "method": "exact_id", "aliases": ["loan_id", "loan_number", "application_id"]},
    ],
}

__all__ = [
    "ADDRESS_MATCH_ALGO",
    "BROKEN_PERIOD_INTEREST_TOLERANCE_PCT",
    "CHECKER_MIN_CONFIDENCE_THRESHOLD",
    "CHECKER_REQUIRED_DOCUMENTS",
    "CHECKER_REQUIRED_LOS_FIELDS",
    "DISBURSAL_MEMO_THRESHOLD_PCT",
    "FACE_MATCH_BAND",
    "FACE_REVIEW_LOWER",
    "FUNDING_AMOUNT_SOURCE_FIELD",
    "FUZZY_MATCH_BAND",
    "FUZZY_PARTIAL_LOWER",
    "LOAN_AMOUNT_THRESHOLD_PCT",
    "MAX_CHECKER_RETRIES",
    "NAME_MATCH_ALGO",
    "NEO_LOS_FIELD_MAP",
    "NODE3A_FIELD_CHECKS",
    "NODE3B_FIELD_CHECKS",
    "NODE3C_FIELD_CHECKS",
]



