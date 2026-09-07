"""Checkpoint builders facade for backwards compatibility.

Exports all 12 checkpoint builders decomposed cleanly into domain sub-modules:
- checkpoints.financial (CP 1, 2, 7, 8, 10, 11)
- checkpoints.identity_kyc (CP 3, 4, 5, 9)
- checkpoints.legal_compliance (CP 6, 12)
"""
from __future__ import annotations

from .checkpoints import (
    build_aadhaar_xml_checkpoint,
    build_all_checkpoints,
    build_application_form_checkpoint,
    build_bt_details_checkpoint,
    build_bpi_checkpoint,
    build_disbursal_memo_checkpoint,
    build_kfs_checkpoint,
    build_kyc_checkpoint,
    build_loan_agreement_checkpoint,
    build_loan_amount_checkpoint,
    build_loan_validity_checkpoint,
    build_sanction_letter_checkpoint,
    build_selfie_checkpoint,
)

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
