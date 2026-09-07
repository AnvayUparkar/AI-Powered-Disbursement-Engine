"""Domain-driven modular checkpoint builders for loan verification."""
from __future__ import annotations

from typing import Any

from ..case_context import CaseContext
from .financial import (
    build_bpi_checkpoint,
    build_disbursal_memo_checkpoint,
    build_kfs_checkpoint,
    build_loan_amount_checkpoint,
    build_loan_validity_checkpoint,
    build_sanction_letter_checkpoint,
)
from .identity_kyc import (
    build_aadhaar_xml_checkpoint,
    build_application_form_checkpoint,
    build_kyc_checkpoint,
    build_selfie_checkpoint,
)
from .legal_compliance import (
    build_bt_details_checkpoint,
    build_loan_agreement_checkpoint,
)


def build_all_checkpoints(ctx: CaseContext) -> list[dict[str, Any]]:
    """Builds the complete ordered list of 12 checkpoints across all domains."""
    return [
        build_loan_amount_checkpoint(ctx),       # CP 1: Financial
        build_loan_validity_checkpoint(ctx),     # CP 2: Financial
        build_application_form_checkpoint(ctx),  # CP 3: Identity & KYC
        build_kyc_checkpoint(ctx),               # CP 4: Identity & KYC
        build_selfie_checkpoint(ctx),            # CP 5: Identity & KYC
        build_loan_agreement_checkpoint(ctx),    # CP 6: Legal & Compliance
        build_kfs_checkpoint(ctx),               # CP 7: Financial
        build_sanction_letter_checkpoint(ctx),   # CP 8: Financial
        build_aadhaar_xml_checkpoint(ctx),       # CP 9: Identity & KYC
        build_bpi_checkpoint(ctx),               # CP 10: Financial
        build_disbursal_memo_checkpoint(ctx),    # CP 11: Financial
        build_bt_details_checkpoint(ctx),        # CP 12: Legal & Compliance
    ]


__all__ = [
    "build_loan_amount_checkpoint",
    "build_loan_validity_checkpoint",
    "build_application_form_checkpoint",
    "build_kyc_checkpoint",
    "build_selfie_checkpoint",
    "build_loan_agreement_checkpoint",
    "build_kfs_checkpoint",
    "build_sanction_letter_checkpoint",
    "build_aadhaar_xml_checkpoint",
    "build_bpi_checkpoint",
    "build_disbursal_memo_checkpoint",
    "build_bt_details_checkpoint",
    "build_all_checkpoints",
]
