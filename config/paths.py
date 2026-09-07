"""Paths configuration — canonical storage directory structures and timezone constants."""
from datetime import timedelta, timezone
from pathlib import Path

# Indian Standard Time (IST, UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))

# Base Repository Root & Data Directories
BASE_DIR = Path(__file__).resolve().parent.parent
POC_DATA_DIR = BASE_DIR / "poc_data"

# LOS Storage Tiers
LOS_DIR = POC_DATA_DIR / "los"
LOS_LOANS_DIR = LOS_DIR / "loans"
LOS_RECEIVED_DIR = LOS_DIR / "scorecards_received"

# DMS Source Tier
DMS_DIR = POC_DATA_DIR / "dms"

# Pipeline S3 Storage Tiers
S3_LOS_DIR = POC_DATA_DIR / "s3_los"
S3_RAW_DIR = POC_DATA_DIR / "s3_raw"
S3_EXTRACTED_DIR = POC_DATA_DIR / "s3_extracted"
S3_EXTRACTED_STRUCTURED_DIR = POC_DATA_DIR / "s3_extracted_structured"
S3_RESULT_DIR = POC_DATA_DIR / "s3_result"

# Ensure canonical directory structures exist
for d in (
    POC_DATA_DIR,
    LOS_DIR,
    LOS_LOANS_DIR,
    LOS_RECEIVED_DIR,
    DMS_DIR,
    S3_LOS_DIR,
    S3_RAW_DIR,
    S3_EXTRACTED_DIR,
    S3_EXTRACTED_STRUCTURED_DIR,
    S3_RESULT_DIR,
):
    d.mkdir(parents=True, exist_ok=True)
