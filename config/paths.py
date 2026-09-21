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

# Trusted Root Certificates (Indian CCA & PKI)
TRUSTED_ROOTS_DIR = BASE_DIR / "config" / "trusted_roots"

# IDP Storage Tiers (Mock S3 on disk)
IDP_TEMP_DIR = POC_DATA_DIR / "idp_temp"
IDP_S3_MOCK_DIR = IDP_TEMP_DIR / "s3_mock" / "disbursement-documents"
IDP_PARSED_DIR = IDP_S3_MOCK_DIR / "parsed-documents"
IDP_RAW_DIR = IDP_S3_MOCK_DIR / "raw-documents"

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
    TRUSTED_ROOTS_DIR,
    IDP_TEMP_DIR,
    IDP_S3_MOCK_DIR,
    IDP_PARSED_DIR,
    IDP_RAW_DIR,
):
    d.mkdir(parents=True, exist_ok=True)


