"""Legal & Compliance checkpoints: Loan Agreement (e-signature & OTP), Balance Transfer (BT) Details."""
from __future__ import annotations

from typing import Any

from ..case_context import (
    CaseContext,
    build_checkpoint,
    build_evidence,
    build_field,
    compute_checkpoint_confidence,
    resolve_field_confidence,
)


def build_loan_agreement_checkpoint(ctx: CaseContext) -> dict[str, Any]:
    """CP 6: Loan Agreement presence, digital e-signature, and OTP audit trail."""
    r6_sig = ctx.records_by_id.get("chk_loan_agreement_digital_signature")
    agree_doc = ctx.get_doc("loan_agreement")

    doc_present = agree_doc.get("loan_agreement_present")
    doc_signed = agree_doc.get("loan_agreement_signed")
    if doc_present is None:
        for d in ctx.docs.values():
            if isinstance(d, dict) and d.get("loan_agreement_present") is not None:
                doc_present = d.get("loan_agreement_present")
                doc_signed = d.get("loan_agreement_signed")
                break

    has_agree = bool(doc_present) if doc_present is not None else (
        "loan_agreement" in ctx.docs
        or ctx.has_doc_matching("agreement")
        or (ctx.dms_dir / "loan_agreement_otp_audit.json").exists()
    )
    is_signed = bool(doc_signed) if doc_signed is not None else (
        (r6_sig is not None and r6_sig.get("match_status") != "MISMATCH")
        or (ctx.dms_dir / "loan_agreement_otp_audit.json").exists()
    )

    fields: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    agree_records = [r for r in ctx.records if "agreement" in (r.get("check_id") or "").lower() or "signature" in (r.get("check_id") or "").lower()]
    r6_presence = ctx.records_by_id.get("chk_loan_agreement_presence")
    sig_conf = resolve_field_confidence(doc=agree_doc, field_name="signature", record=r6_sig)
    presence_conf = resolve_field_confidence(doc=agree_doc, field_name="presence", record=r6_presence) if has_agree else 0.0

    if has_agree and is_signed:
        status = "VERIFIED"
        fields = [
            build_field("Loan Agreement Presence", "Present", presence_conf, f"doc-{ctx.loan_id}-agreement"),
            build_field("Loan Agreement Signature", "Signed", sig_conf, f"doc-{ctx.loan_id}-agreement"),
        ]
        evidence = [build_evidence(f"doc-{ctx.loan_id}-agreement", "Loan_Agreement.pdf", "Loan Agreement — Signature", 1, "Agreement Signature")]
        val = {"left": "Present & Signed", "right": "Mandatory Signed Agreement", "result": "MATCH", "leftSource": "loan_agreement", "rightSource": "mandatory"}
        notes = "Loan agreement present and digitally signed."
    elif has_agree and not is_signed:
        status = "DISCREPANCY"
        fields = [
            build_field("Loan Agreement Presence", "Present", presence_conf, f"doc-{ctx.loan_id}-agreement"),
            build_field("Loan Agreement Signature", "Unsigned", 0.0, f"doc-{ctx.loan_id}-agreement"),
        ]
        evidence = [build_evidence(f"doc-{ctx.loan_id}-agreement", "Loan_Agreement.pdf", "Loan Agreement — Unsigned", 1, "Agreement Signature")]
        val = {"left": "Present & Unsigned", "right": "Mandatory Signed Agreement", "result": "MISMATCH", "leftSource": "loan_agreement", "rightSource": "mandatory"}
        notes = "Loan agreement uploaded but missing required digital signature."
    else:
        status = "INDETERMINATE"
        fields = [build_field("Loan Agreement", "Not Uploaded", 0.0, f"doc-{ctx.loan_id}")]
        evidence = []
        val = {"left": "Missing", "right": "Mandatory Signed Agreement", "result": "MISMATCH", "leftSource": "loan_agreement", "rightSource": "mandatory"}
        notes = "Loan agreement not uploaded."

    conf = compute_checkpoint_confidence(fields, agree_records) if status == "VERIFIED" else 0.0

    return build_checkpoint(
        6,
        "Loan Agreement",
        status,
        conf,
        notes,
        "Loan agreement presence and execution signature verification.",
        fields,
        evidence,
        val,
        comparisons=agree_records,
    )


def build_bt_details_checkpoint(ctx: CaseContext) -> dict[str, Any]:
    """CP 12: Balance Transfer (BT) details conditional validation."""
    bt_records = [
        r for r in ctx.records
        if "bt" in (r.get("check_id") or "").lower()
        or "balance_transfer" in (r.get("field") or "").lower()
    ]

    if ctx.is_bt:
        bt_doc = ctx.get_doc("bt_details", "bt")
        has_bt_doc = bool(bt_doc) or ctx.has_doc_matching("bt", "foreclosure")
        if has_bt_doc:
            bt_presence_conf = resolve_field_confidence(
                doc=bt_doc,
                field_name="presence",
                record=bt_records[0] if bt_records else None,
            )
            bt_fields = [
                build_field("Balance Transfer", "1 (Applicable)", 100.0, f"doc-{ctx.loan_id}-bt"),
                build_field("BT Details Presence", "Present", bt_presence_conf, f"doc-{ctx.loan_id}-bt"),
            ]
            bt_conf = compute_checkpoint_confidence(bt_fields, bt_records)
            return build_checkpoint(
                12,
                "BT Details",
                "VERIFIED",
                bt_conf,
                "BT details document present and verified.",
                "BT Details required for Balance Transfer loans.",
                bt_fields,
                [build_evidence(f"doc-{ctx.loan_id}-bt", "BT_Details.pdf", "BT Details Document", 1, "Previous Lender")],
                {"left": "Present", "right": "Mandatory for BT", "result": "MATCH", "leftSource": "bt_details", "rightSource": "los"},
                comparisons=bt_records,
            )
        return build_checkpoint(
            12,
            "BT Details",
            "INDETERMINATE",
            0.0,
            "Balance Transfer loan flagged in LOS (BT=1), but BT Details document is missing.",
            "BT Details required for Balance Transfer loans.",
            [
                build_field("Balance Transfer", "1 (Applicable)", 100.0, f"doc-{ctx.loan_id}-bt"),
                build_field("BT Details Presence", "Missing", 0.0, f"doc-{ctx.loan_id}-bt"),
            ],
            [],
            {"left": "Missing", "right": "Mandatory for BT", "result": "MISMATCH", "leftSource": "bt_details", "rightSource": "los"},
            comparisons=bt_records,
        )

    return build_checkpoint(
        12,
        "BT Details",
        "NOT_APPLICABLE",
        0.0,
        "Not applicable — not a BT case (BT flag = 0).",
        "BT Details required for Balance Transfer loans.",
        [build_field("Balance Transfer", "0 (Not Applicable)", 100.0, f"doc-{ctx.loan_id}-bt")],
        [],
        {"left": "0 (Non-BT)", "right": "Not Applicable", "result": "MATCH", "leftSource": "los", "rightSource": "los"},
        comparisons=bt_records,
    )
