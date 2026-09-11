"""Application and verification settings — thresholds, algorithms, and API configuration."""
import os
from dotenv import load_dotenv
from config.paths import BASE_DIR

# Load local environment if present
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Verification Algorithms & Fuzzy Match Thresholds
NAME_MATCH_ALGO = "jaro_winkler"
ADDRESS_MATCH_ALGO = "tfidf_cosine"

FUZZY_MATCH_BAND = float(os.getenv("FUZZY_MATCH_BAND", "0.5"))          # >= 0.92: auto MATCH
FUZZY_PARTIAL_LOWER = float(os.getenv("FUZZY_PARTIAL_LOWER", "0.4"))   # 0.75 - 0.92: PARTIAL -> LLM, < 0.75: MISMATCH

FACE_MATCH_BAND = float(os.getenv("FACE_MATCH_BAND", "0.90"))           # >= 0.90: auto MATCH
FACE_REVIEW_LOWER = float(os.getenv("FACE_REVIEW_LOWER", "0.75"))       # 0.75 - 0.90: PARTIAL/REVIEW, < 0.75: MISMATCH

# Financial & Policy Thresholds
LOAN_AMOUNT_THRESHOLD_PCT = float(os.getenv("LOAN_AMOUNT_THRESHOLD_PCT", "0.90"))
DISBURSAL_MEMO_THRESHOLD_PCT = float(os.getenv("DISBURSAL_MEMO_THRESHOLD_PCT", "0.90"))
BROKEN_PERIOD_INTEREST_TOLERANCE_PCT = float(os.getenv("BROKEN_PERIOD_INTEREST_TOLERANCE_PCT", "0.10"))
FUNDING_AMOUNT_SOURCE_FIELD = "funding_amount"

# Gemini LLM Adjudication Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.0"))

# LLM Field Extraction Configuration (OpenRouter / Gemini)
LLM_API_KEY = os.getenv("LLM_API_KEY") or GEMINI_API_KEY
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemini-2.5-flash-lite")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")

# Max workers for document & node processing
MAX_DOC_WORKERS = int(os.getenv("MAX_DOC_WORKERS", "4"))

# Fast-track pipeline bypass: Skip IDP OCR & LLM structuring when structured data is already staged
SKIP_IDP = os.getenv("SKIP_IDP", "false").lower() in ("true", "1", "yes")

# When True, idp_scan always re-runs Docling/OCR from the raw document instead of reusing a
# previously-written S3_EXTRACTED_DIR/{loan_id}/{doc_key}.json result (see pipeline/nodes/idp_scan.py).
# Turn on while iterating on Docling/OCR config so "Run Verification Engine" reflects current code
# instead of replaying a stale extraction from an earlier run.
DISABLE_IDP_EXTRACTION_CACHE = os.getenv("DISABLE_IDP_EXTRACTION_CACHE", "false").lower() in ("true", "1", "yes")

# Field weight strategy: When True, all fields in all documents receive equal weight (1.0).
# When False (default), the tiered criticality weights (3.0, 2.0, 1.0) are applied.
USE_EQUAL_FIELD_WEIGHTS = os.getenv("USE_EQUAL_FIELD_WEIGHTS", "false").lower() in ("true", "1", "yes")

