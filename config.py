import os
from pathlib import Path

from dotenv import load_dotenv

# Base Directory & Environment Configuration
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Data & Storage Paths
POC_DATA_DIR = BASE_DIR / "poc_data"

LOS_DIR = POC_DATA_DIR / "los"
LOS_LOANS_DIR = LOS_DIR / "loans"
LOS_RECEIVED_DIR = LOS_DIR / "scorecards_received"

DMS_DIR = POC_DATA_DIR / "dms"
S3_RAW_DIR = POC_DATA_DIR / "s3_raw"
S3_EXTRACTED_DIR = POC_DATA_DIR / "s3_extracted"
S3_RESULT_DIR = POC_DATA_DIR / "s3_result"

# Gemini LLM Adjudication Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.0"))

# OpenRouter LLM Field Extraction Configuration (Node 2 OCR → structured JSON)
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemini-2.0-flash-lite")


