"""Paths configuration — canonical storage directory structures and timezone constants."""
from datetime import timedelta, timezone
from pathlib import Path

from config.tenant import TenantPath

# Indian Standard Time (IST, UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))

# Base Repository Root & Data Directories
BASE_DIR = Path(__file__).resolve().parent.parent
POC_DATA_DIR = BASE_DIR / "poc_data"

# Per-tenant storage tiers: each resolves to poc_data/tenants/<tenant_id>/<subdir> for the
# tenant bound to the current request/task (see config/tenant.py). They are never shared.

# LOS Storage Tiers
LOS_DIR = TenantPath("los")
LOS_LOANS_DIR = TenantPath("los/loans")
LOS_RECEIVED_DIR = TenantPath("los/scorecards_received")

# DMS Source Tier
DMS_DIR = TenantPath("dms")

# Pipeline S3 Storage Tiers
S3_LOS_DIR = TenantPath("s3_los")
S3_RAW_DIR = TenantPath("s3_raw")
S3_EXTRACTED_DIR = TenantPath("s3_extracted")
S3_EXTRACTED_STRUCTURED_DIR = TenantPath("s3_extracted_structured")
S3_RESULT_DIR = TenantPath("s3_result")

# Trusted Root Certificates (Indian CCA & PKI)
TRUSTED_ROOTS_DIR = BASE_DIR / "config" / "trusted_roots"

# System-wide (non-tenant) settings — deliberately global, not under poc_data/tenants/<id>/,
# since flags here (e.g. the DGCL pipeline kill-switch) apply across every tenant. Lives on the
# same shared volume as everything else in poc_data so api/worker/idp pods all see the same value.
SYSTEM_DIR = POC_DATA_DIR / "system"
PIPELINE_FLAGS_FILE = SYSTEM_DIR / "pipeline_flags.json"

# Ensure shared (non-tenant) directory structures exist. Tenant directories are created lazily
# on first access by TenantPath.
for d in (
    POC_DATA_DIR,
    TRUSTED_ROOTS_DIR,
    SYSTEM_DIR,
):
    d.mkdir(parents=True, exist_ok=True)


