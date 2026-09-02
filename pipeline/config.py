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

__all__ = [
    "ADDRESS_MATCH_ALGO",
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
]


