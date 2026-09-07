"""Pipeline verification checks configuration — field match specifications and 12-checkpoint metadata."""
from typing import Any, Dict, List

# ── NEO LOS DB column -> canonical field name ──────────────────────────────
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
}

# ── Centralized Field Aliases (Single Source of Truth) ────────────────────────
FIELD_ALIASES: dict[str, list[str]] = {
    "loan_validity": ["tenure", "tenure_months", "loan_tenure", "loan_term", "term", "validity"],
    "loan_amount": ["funding_amount", "amount", "sanctioned_amount", "disbursal_amount"],
    "pan_number": ["pan", "applicant_pan_number"],
    "aadhaar_number": ["aadhaar", "aadhaar_no", "uid"],
    "applicant_name": ["customer_name", "name", "full_name"],
    "fathers_name": ["father_name"],
    "account_no": ["bank_account_no", "account_number", "applicant_bank_account_no"],
    "current_address": ["address", "address_text", "permanent_address"],
    "application_no": ["application_id", "loan_id", "loan_no"],
    "dob": ["applicant_dob", "date_of_birth"],
    "mobile_no": ["applicant_mobile_no", "mobile", "phone"],
}

# ── Field Criticality Weights for Scorecard Calculations ──────────────────────
# Tier 1 (3.0): Core identity and regulatory hard gates
# Tier 2 (2.0): Core loan financials
# Tier 3 (1.0): Contact, demographic, and metadata
FIELD_CRITICALITY_WEIGHTS: dict[str, float] = {
    # Tier 1: Core Identity
    "applicant_name": 3.0,
    "customer_name": 3.0,
    "pan_number": 3.0,
    "pan": 3.0,
    "applicant_pan_number": 3.0,
    "dob": 3.0,
    "date_of_birth": 3.0,
    "applicant_dob": 3.0,
    "aadhaar_number": 3.0,
    "aadhaar_no": 3.0,
    # Tier 2: Core Financials
    "loan_amount": 2.0,
    "requested_amount": 2.0,
    "sanctioned_amount": 2.0,
    "funding_amount": 2.0,
    "tenure": 2.0,
    "tenure_months": 2.0,
    "requested_tenure": 2.0,
    "loan_validity": 2.0,
    "account_no": 2.0,
    "bank_account_no": 2.0,
    "applicant_bank_account_no": 2.0,
    "bpi_charges": 2.0,
    "bpi": 2.0,
    "emi": 2.0,
    # Tier 3: Contact & Demographic
    "mobile_no": 1.0,
    "applicant_mobile_no": 1.0,
    "gender": 1.0,
    "applicant_gender": 1.0,
    "fathers_name": 1.0,
    "address": 1.0,
    "current_address": 1.0,
    "permanent_address": 1.0,
    "loan_type": 1.0,
    "application_no": 1.0,
    "application_date": 1.0,
    "account_type": 1.0,
    "type_of_account": 1.0,
}

# ── 1. KYC Field Checks (KYC Checker Node) ───────────────────────────────────
KYC_FIELD_CHECKS: dict[str, list[dict[str, Any]]] = {
    "aadhaar": [
        {"doc_field": "applicant_name", "los_field": "applicant_name", "method": "jaro_winkler", "aliases": ["name", "full_name"]},
        {"doc_field": "fathers_name", "los_field": "fathers_name", "method": "jaro_winkler", "aliases": ["father_name"]},
        {"doc_field": "gender", "los_field": "applicant_gender", "method": "exact_string_ci", "aliases": ["applicant_gender"]},
        {"doc_field": "address", "los_field": "current_address", "method": "tfidf_cosine", "aliases": ["current_address", "address_text"]},
        {"doc_field": "aadhaar_number", "los_field": "aadhaar_no", "method": "masked_aadhaar", "aliases": ["aadhaar", "aadhaar_no", "uid"]},
        {"doc_field": "mobile_no", "los_field": "applicant_mobile_no", "method": "exact_string", "aliases": ["applicant_mobile_no", "mobile", "phone"]},
        {"doc_field": "dob", "los_field": "applicant_dob", "method": "exact_date", "aliases": ["applicant_dob", "date_of_birth"]},
    ],
    "pan": [
        {"doc_field": "applicant_name", "los_field": "applicant_name", "method": "jaro_winkler", "aliases": ["name", "full_name"]},
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


# ── 2. Financial Field Checks (Financial Checker Node) ────────────────────────
FINANCIAL_FIELD_CHECKS: dict[str, list[dict[str, Any]]] = {
    "application_form": [
        {"doc_field": "loan_amount", "los_field": "loan_amount", "method": "threshold_90", "aliases": ["funding_amount", "amount"]},
        {"doc_field": "loan_validity", "los_field": "loan_validity", "method": "tenure_months", "aliases": ["tenure", "tenure_months", "loan_tenure", "loan_term", "term", "validity"]},
        {"doc_field": "account_no", "los_field": "applicant_bank_account_no", "method": "exact_id", "aliases": ["account_number", "applicant_bank_account_no", "bank_account_no"]},
        {"doc_field": "type_of_account", "los_field": "bank_account_type", "method": "exact_string_ci", "aliases": ["bank_account_type", "account_type"]},
        {"doc_field": "loan_type", "los_field": "loan_type", "method": "exact_string_ci", "aliases": []},
        {"doc_field": "current_address", "los_field": "current_address", "method": "tfidf_cosine", "aliases": ["address", "address_text"]},
    ],
    "kfs": [
        {"doc_field": "loan_amount", "los_field": "loan_amount", "method": "threshold_90", "aliases": ["funding_amount", "amount"]},
        {"doc_field": "loan_validity", "los_field": "loan_validity", "method": "tenure_months", "aliases": ["tenure", "tenure_months", "loan_tenure", "loan_term", "term", "validity"]},
        {"doc_field": "loan_type", "los_field": "loan_type", "method": "exact_string_ci", "aliases": []},
        {"doc_field": "irr_percent", "los_field": "irr_percent", "method": "exact_numeric", "aliases": ["irr", "roi", "interest_rate"]},
        {"doc_field": "emi", "los_field": "emi", "method": "exact_numeric", "aliases": ["monthly_emi", "emi_amount"]},
        {"doc_field": "customer_consent", "los_field": None, "method": "presence_only", "aliases": ["consent", "is_consented"]},
    ],
    "disbursal_memo": [
        {"doc_field": "loan_no", "los_field": "loan_id", "method": "exact_id", "aliases": ["loan_id", "loan_number", "application_id"]},
        {"doc_field": "loan_amount", "los_field": "loan_amount", "method": "threshold_90", "aliases": ["disbursal_amount", "amount"]},
    ],
    "sanction_letter": [
        {"doc_field": "applicant_name", "los_field": "applicant_name", "method": "jaro_winkler", "aliases": ["name"]},
        {"doc_field": "loan_amount", "los_field": "loan_amount", "method": "threshold_90", "aliases": ["funding_amount", "amount", "sanctioned_amount"]},
        {"doc_field": "loan_validity", "los_field": "loan_validity", "method": "tenure_months", "aliases": ["tenure", "tenure_months", "loan_tenure", "loan_term", "term", "validity"]},
        {"doc_field": "irr_percent", "los_field": "irr_percent", "method": "exact_numeric", "aliases": ["irr", "roi", "interest_rate"]},
        {"doc_field": "emi", "los_field": "emi", "method": "exact_numeric", "aliases": ["monthly_emi", "emi_amount"]},
        {"doc_field": "loan_type", "los_field": "loan_type", "method": "exact_string_ci", "aliases": []},
        {"doc_field": "mobile_no", "los_field": "applicant_mobile_no", "method": "exact_string", "aliases": ["applicant_mobile_no", "mobile", "phone"]},
        {"doc_field": "address", "los_field": "current_address", "method": "tfidf_cosine", "aliases": ["current_address", "address_text"]},
    ],
    "account_statement": [
        {"doc_field": "current_address", "los_field": "current_address", "method": "tfidf_cosine", "aliases": ["address", "address_text"]},
    ],
}

# ── 3. Loan Application Field Checks (Loan App Checker Node) ─────────────────
LOAN_APP_FIELD_CHECKS: dict[str, list[dict[str, Any]]] = {
    "application_form": [
        {"doc_field": "application_date", "los_field": "application_date", "method": "exact_date", "aliases": ["date_of_application", "app_date"]},
        {"doc_field": "application_no", "los_field": "loan_id", "method": "exact_id", "aliases": ["application_id", "loan_id"]},
    ],
    "kfs": [
        {"doc_field": "application_no", "los_field": "loan_id", "method": "exact_id", "aliases": ["application_id", "loan_id"]},
        {"doc_field": "application_date", "los_field": "application_date", "method": "exact_date", "aliases": ["date_of_application", "app_date"]},
    ],
    "sanction_letter": [
        {"doc_field": "application_no", "los_field": "loan_id", "method": "exact_id", "aliases": ["application_id", "loan_id"]},
        {"doc_field": "application_date", "los_field": "application_date", "method": "exact_date", "aliases": ["date_of_application", "app_date"]},
    ],
    "disbursal_memo": [
        {"doc_field": "loan_no", "los_field": "loan_id", "method": "exact_id", "aliases": ["loan_id", "loan_number", "application_id"]},
    ],
}


# ── 12 DGSC Checkpoints Specification ─────────────────────────────────────────
# Used for generating the scorecard report and standardizing frontend serialization.
CHECKPOINTS_SPEC: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Applicant Identity Verification",
        "checker": "check_kyc",
        "doc_types": ["aadhaar", "pan"],
        "fields": ["applicant_name", "dob", "pan_number"],
        "rule": "Applicant Name, DOB, and PAN must match LOS records across Aadhaar and PAN Card.",
        "category": "KYC",
        "weight": 10.0,
    },
    {
        "id": 2,
        "name": "Address & Demographics Verification",
        "checker": "check_kyc",
        "doc_types": ["aadhaar"],
        "fields": ["address", "aadhaar_number"],
        "rule": "Current address text similarity >= 0.92 and Aadhaar number match.",
        "category": "KYC",
        "weight": 8.0,
    },
    {
        "id": 3,
        "name": "PAN & Father's Name Match",
        "checker": "check_kyc",
        "doc_types": ["pan"],
        "fields": ["fathers_name", "pan_number"],
        "rule": "Father's name fuzzy similarity >= 0.92 and PAN format compliance.",
        "category": "KYC",
        "weight": 7.0,
    },
    {
        "id": 4,
        "name": "Bank Account & Disbursal Ownership",
        "checker": "check_financial",
        "doc_types": ["account_statement"],
        "fields": ["account_no", "applicant_name", "pan_number"],
        "rule": "Bank account number and applicant name match LOS disbursement bank details.",
        "category": "Financial",
        "weight": 10.0,
    },
    {
        "id": 5,
        "name": "Loan Amount & Funding Consistency",
        "checker": "check_financial",
        "doc_types": ["disbursal_memo"],
        "fields": ["loan_amount", "loan_no"],
        "rule": "Disbursal memo loan amount must match LOS requested amount within 90% threshold.",
        "category": "Financial",
        "weight": 10.0,
    },
    {
        "id": 6,
        "name": "Broken Period Interest (BPI) Consistency",
        "checker": "check_financial",
        "doc_types": ["kfs", "disbursal_memo"],
        "fields": ["bpi_charge"],
        "rule": "BPI amount in KFS must align with Disbursal Memo within 10% tolerance.",
        "category": "Financial",
        "weight": 5.0,
    },
    {
        "id": 7,
        "name": "Application Form Integrity",
        "checker": "check_loan_application",
        "doc_types": ["application_form"],
        "fields": ["applicant_name", "loan_amount", "pan_number"],
        "rule": "Loan application form must contain matching applicant name, amount, and PAN.",
        "category": "Loan Application",
        "weight": 10.0,
    },
    {
        "id": 8,
        "name": "Application Terms & Validity",
        "checker": "check_loan_application",
        "doc_types": ["application_form"],
        "fields": ["loan_validity", "loan_type", "type_of_account"],
        "rule": "Loan tenure and loan scheme must match approved terms in LOS.",
        "category": "Loan Application",
        "weight": 7.0,
    },
    {
        "id": 9,
        "name": "KFS Financial Terms Match",
        "checker": "check_loan_application",
        "doc_types": ["kfs"],
        "fields": ["loan_amount", "loan_validity"],
        "rule": "Key Fact Statement terms (amount, tenure) must match LOS.",
        "category": "Loan Application",
        "weight": 10.0,
    },
    {
        "id": 10,
        "name": "KFS Borrower Consent Verification",
        "checker": "check_loan_application",
        "doc_types": ["kfs"],
        "fields": ["customer_consent"],
        "rule": "Explicit customer acceptance/consent must be recorded on KFS.",
        "category": "Loan Application",
        "weight": 8.0,
    },
    {
        "id": 11,
        "name": "Sanction Letter Terms Consistency",
        "checker": "check_loan_application",
        "doc_types": ["sanction_letter"],
        "fields": ["applicant_name", "loan_amount"],
        "rule": "Sanction letter approved amount and name must match LOS approval.",
        "category": "Loan Application",
        "weight": 8.0,
    },
    {
        "id": 12,
        "name": "Disbursement Authorization & Audit",
        "checker": "check_loan_application",
        "doc_types": ["application_form", "disbursal_memo"],
        "fields": ["application_date", "application_no"],
        "rule": "Application date, loan ID, and disbursement lifecycle dates must align.",
        "category": "Loan Application",
        "weight": 7.0,
    },
]
