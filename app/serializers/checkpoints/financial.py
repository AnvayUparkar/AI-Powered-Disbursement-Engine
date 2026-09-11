"""Financial checkpoints: Loan Amount, Loan Validity, KFS, Sanction Letter, BPI, Disbursal Memo."""
from __future__ import annotations

from typing import Any

from pipeline.engines.comparison import extract_field_value

from ..case_context import (
    CaseContext,
    build_checkpoint,
    build_evidence,
    build_field,
    compute_checkpoint_confidence,
    format_tenure_months,
    inr_format,
    resolve_checkpoint_validation,
    resolve_field_confidence,
    safe_float,
)


def build_loan_amount_checkpoint(ctx: CaseContext) -> dict[str, Any]:
    """CP 1: Loan Amount consistency across agreement, sanction, and KFS."""
    r_kfs_amt = ctx.get_check_record("chk_check_financial_kfs_loan_amount_vs_los", "chk_kfs_vs_los_funding")
    r_app_amt = ctx.get_check_record(
        "chk_check_financial_application_form_loan_amount_vs_los",
        "chk_check_loan_application_application_form_loan_amount_vs_los",
        "chk_loan_amt_application_form_vs_kfs",
    )
    r_sanc_amt = ctx.get_check_record(
        "chk_check_financial_sanction_letter_loan_amount_vs_los",
        "chk_sanction_vs_los_funding",
    )
    app_form = ctx.get_doc("application_form", "appform")
    kfs_doc = ctx.get_doc("kfs")
    sanction_doc = ctx.get_doc("sanction_letter", "sanction")

    amount_records = [
        r for r in ctx.records
        if r.get("field") in ("loan_amount", "funding_amount")
        or (r.get("check_id") and "loan_amount" in r.get("check_id", "").lower())
    ]

    fields: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    if app_form.get("loan_amount") is not None:
        app_amt = safe_float(app_form.get("loan_amount"))
        app_conf = resolve_field_confidence(doc=app_form, field_name="loan_amount", record=r_app_amt)
        fields.append(build_field("Application Amount", inr_format(app_amt), app_conf, f"doc-{ctx.loan_id}-appform"))
        evidence.append(build_evidence(f"doc-{ctx.loan_id}-appform", "Application_Form.pdf", "Application Form — Amount", 1, "Loan Amount"))

    if kfs_doc.get("loan_amount") is not None:
        kfs_conf = resolve_field_confidence(doc=kfs_doc, field_name="loan_amount", record=r_kfs_amt)
        fields.append(build_field("KFS Amount", inr_format(kfs_doc["loan_amount"]), kfs_conf, f"doc-{ctx.loan_id}-kfs"))
        evidence.append(build_evidence(f"doc-{ctx.loan_id}-kfs", "KFS.pdf", "KFS — Amount", 1, "Loan Amount"))

    if sanction_doc.get("loan_amount") is not None:
        sanc_conf = resolve_field_confidence(doc=sanction_doc, field_name="loan_amount", record=r_sanc_amt)
        fields.append(build_field("Sanction Amount", inr_format(sanction_doc["loan_amount"]), sanc_conf, f"doc-{ctx.loan_id}-sanction"))
        evidence.append(build_evidence(f"doc-{ctx.loan_id}-sanction", "Sanction_Letter.pdf", "Sanction Letter — Amount", 1, "Loan Amount"))

    has_amt_mismatch = any(r.get("match_status") == "MISMATCH" for r in amount_records)

    primary_record = r_kfs_amt or r_sanc_amt or r_app_amt
    if not fields:
        fields = [build_field("Loan Amount", "Not Available", 0.0, f"doc-{ctx.loan_id}")]
        status = "INDETERMINATE"
        notes = "No loan documents available for amount verification."
    elif has_amt_mismatch:
        status = "DISCREPANCY"
        notes = (primary_record.get("notes") if primary_record else "") or "Loan amount discrepancy across documents."
    else:
        status = "VERIFIED"
        notes = (primary_record.get("notes") if primary_record else "") or (
            f"Loan amount {inr_format(ctx.loan_amount)} consistent across all available documents."
        )

    has_loan_amt = bool(fields and any(f["confidence"] is not None and f["confidence"] > 0 for f in fields))
    conf = compute_checkpoint_confidence(fields, amount_records) if has_loan_amt else 0.0

    sanction_amt = sanction_doc.get("loan_amount")
    right_val = inr_format(sanction_amt) if sanction_amt else "N/A"
    default_left = inr_format(ctx.loan_amount) if ctx.loan_amount > 0 else "N/A"

    mismatched_amt = next(
        (r for r in amount_records if r.get("match_status") == "MISMATCH" or r.get("result") == "MISMATCH"),
        None,
    )
    if status == "DISCREPANCY" and mismatched_amt:
        vals = mismatched_amt.get("values") or []
        srcs = mismatched_amt.get("sources") or []
        v0 = inr_format(vals[0]) if len(vals) > 0 and vals[0] is not None else default_left
        v1 = inr_format(vals[1]) if len(vals) > 1 and vals[1] is not None else right_val
        val_block = resolve_checkpoint_validation(
            status,
            default_left=v0,
            default_right=v1,
            records=None,
            default_left_source=srcs[0] if len(srcs) > 0 else "application_form",
            default_right_source=srcs[1] if len(srcs) > 1 else "sanction_letter",
        )
    else:
        val_block = resolve_checkpoint_validation(
            status,
            default_left=default_left,
            default_right=right_val,
            records=amount_records,
            default_left_source="los",
            default_right_source="sanction_letter",
        )

    return build_checkpoint(
        1,
        "Loan Amount",
        status,
        conf,
        notes,
        "Loan amount must match exactly between loan agreement, sanction letter, and KFS.",
        fields,
        evidence,
        val_block,
        comparisons=amount_records,
    )


def build_loan_validity_checkpoint(ctx: CaseContext) -> dict[str, Any]:
    """CP 2: Loan Validity and Tenure consistency across documents."""
    r_sanc_tenure = ctx.get_check_record("chk_check_financial_sanction_letter_loan_validity_vs_los")
    r_kfs_tenure = ctx.get_check_record("chk_check_financial_kfs_loan_validity_vs_los")
    r_app_tenure = ctx.get_check_record(
        "chk_check_financial_application_form_loan_validity_vs_los",
        "chk_check_loan_application_application_form_loan_validity_vs_los",
        "chk_loan_validity_tenure",
    )
    app_form = ctx.get_doc("application_form", "appform")
    kfs_doc = ctx.get_doc("kfs")
    sanction_doc = ctx.get_doc("sanction_letter", "sanction")

    tenure_records = [
        r for r in ctx.records
        if r.get("field") in ("loan_validity", "tenure")
        or (r.get("check_id") and "loan_validity" in r.get("check_id", "").lower())
    ]

    app_tenure = extract_field_value(app_form, "loan_validity")
    sanc_tenure = extract_field_value(sanction_doc, "loan_validity")
    kfs_tenure = extract_field_value(kfs_doc, "loan_validity")
    los_tenure = extract_field_value(ctx.los_data, "loan_validity")
    tenure_val = app_tenure or kfs_tenure or los_tenure

    fields: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    if app_tenure is not None:
        app_conf = resolve_field_confidence(doc=app_form, field_name="loan_validity", record=r_app_tenure)
        fields.append(build_field("Tenure Months", f"{app_tenure}", app_conf, f"doc-{ctx.loan_id}-appform"))
        evidence.append(build_evidence(f"doc-{ctx.loan_id}-appform", "Application_Form.pdf", "Application Form — Tenure", 1))

    if sanc_tenure is not None:
        sanc_conf = resolve_field_confidence(doc=sanction_doc, field_name="loan_validity", record=r_sanc_tenure)
        fields.append(build_field("Sanction Tenure", f"{sanc_tenure}", sanc_conf, f"doc-{ctx.loan_id}-sanction"))
        evidence.append(build_evidence(f"doc-{ctx.loan_id}-sanction", "Sanction_Letter.pdf", "Sanction Letter — Tenure", 1))

    if kfs_tenure is not None and kfs_tenure != sanc_tenure:
        kfs_conf = resolve_field_confidence(doc=kfs_doc, field_name="loan_validity", record=r_kfs_tenure)
        fields.append(build_field("KFS Tenure", f"{kfs_tenure}", kfs_conf, f"doc-{ctx.loan_id}-kfs"))
        evidence.append(build_evidence(f"doc-{ctx.loan_id}-kfs", "KFS.pdf", "KFS — Tenure", 1))

    primary_tenure = r_sanc_tenure or r_kfs_tenure or r_app_tenure
    has_validity_mismatch = any(r.get("match_status") == "MISMATCH" for r in tenure_records)

    if not fields:
        fields.append(build_field("Tenure", "Not Available", 0.0, f"doc-{ctx.loan_id}"))
        status = "INDETERMINATE"
        notes = "Tenure documents not uploaded."
    else:
        status = "DISCREPANCY" if has_validity_mismatch else "VERIFIED"
        if not has_validity_mismatch and primary_tenure and primary_tenure.get("match_status") in ("PARTIAL", "NOT_FOUND"):
            status = "INDETERMINATE"
        notes = (primary_tenure.get("notes") if primary_tenure else "") or (
            "Tenure discrepancy detected." if status == "DISCREPANCY" else f"Loan tenure normalized at {tenure_val}."
        )

    has_tenure_amt = bool(fields and any(f["confidence"] is not None and f["confidence"] > 0 for f in fields))
    conf = compute_checkpoint_confidence(fields, tenure_records) if has_tenure_amt else 0.0

    mismatched_tenure = next(
        (r for r in tenure_records if r.get("match_status") == "MISMATCH" or r.get("result") == "MISMATCH"),
        None,
    )
    if status == "DISCREPANCY" and mismatched_tenure:
        vals = mismatched_tenure.get("values") or []
        srcs = mismatched_tenure.get("sources") or []
        v0 = f"{vals[0]} Months" if str(vals[0]).isdigit() else str(vals[0])
        v1 = f"{vals[1]} Months" if str(vals[1]).isdigit() else str(vals[1])
        val_block = resolve_checkpoint_validation(
            status,
            default_left=v0,
            default_right=v1,
            records=None,
            default_left_source=srcs[0] if len(srcs) > 0 else "application_form",
            default_right_source=srcs[1] if len(srcs) > 1 else "los",
        )
    else:
        val_block = resolve_checkpoint_validation(
            status,
            default_left=f"{tenure_val}" if tenure_val is not None else "N/A",
            default_right=f"{sanc_tenure or 'N/A'}",
            records=tenure_records,
            default_left_source="application_form",
            default_right_source="sanction_letter",
        )

    return build_checkpoint(
        2,
        "Loan Validity",
        status,
        conf,
        notes,
        "Sanction tenure must match requested application tenure.",
        fields,
        evidence,
        val_block,
        comparisons=tenure_records,
    )


def build_kfs_checkpoint(ctx: CaseContext) -> dict[str, Any]:
    """CP 7: Key Fact Statement (KFS) terms, IRR, EMI, and borrower consent verification."""
    r7_amt = ctx.get_check_record("chk_check_financial_kfs_loan_amount_vs_los", "chk_kfs_vs_los_funding")
    r7_val = ctx.get_check_record("chk_check_financial_kfs_loan_validity_vs_los")
    r7_irr = ctx.get_check_record("chk_check_financial_kfs_irr_percent_vs_los")
    r7_emi = ctx.get_check_record("chk_check_financial_kfs_emi_vs_los")
    r7_consent = ctx.get_check_record("chk_check_financial_kfs_customer_consent_vs_los")

    kfs_records = [r for r in [r7_amt, r7_val, r7_irr, r7_emi, r7_consent] if r is not None]
    all_kfs_records = [
        r for r in ctx.records
        if "kfs" in (r.get("sources") or [])
        or (r.get("check_id") and "kfs" in r.get("check_id", "").lower())
    ]

    kfs_doc = ctx.get_doc("kfs")
    has_kfs = kfs_doc.get("loan_amount") is not None or ctx.has_doc_matching("kfs")

    fields: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    if has_kfs:
        kfs_raw_amt = kfs_doc.get("loan_amount")
        if kfs_raw_amt is not None:
            amt_conf = resolve_field_confidence(doc=kfs_doc, field_name="loan_amount", record=r7_amt)
            fields.append(build_field("KFS Funding Amount", inr_format(safe_float(kfs_raw_amt)), amt_conf, f"doc-{ctx.loan_id}-kfs"))
        if kfs_doc.get("loan_validity") is not None:
            val_conf = resolve_field_confidence(doc=kfs_doc, field_name="loan_validity", record=r7_val)
            fields.append(build_field("KFS Tenure", format_tenure_months(kfs_doc["loan_validity"]), val_conf, f"doc-{ctx.loan_id}-kfs"))
        if kfs_doc.get("irr_percent") is not None:
            irr_conf = resolve_field_confidence(doc=kfs_doc, field_name="irr_percent", record=r7_irr)
            fields.append(build_field("KFS IRR", f"{safe_float(kfs_doc['irr_percent']):.1f}%", irr_conf, f"doc-{ctx.loan_id}-kfs"))
        if kfs_doc.get("emi") is not None:
            emi_conf = resolve_field_confidence(doc=kfs_doc, field_name="emi", record=r7_emi)
            fields.append(build_field("KFS EMI", inr_format(kfs_doc["emi"]), emi_conf, f"doc-{ctx.loan_id}-kfs"))
        if "customer_consent" in kfs_doc and kfs_doc["customer_consent"] is not None:
            consent_val = "Verified (Consented)" if bool(kfs_doc["customer_consent"]) else "Missing / Not Consented"
            consent_conf = resolve_field_confidence(doc=kfs_doc, field_name="customer_consent", record=r7_consent) if bool(kfs_doc["customer_consent"]) else 0.0
            fields.append(build_field("Customer Consent", consent_val, consent_conf, f"doc-{ctx.loan_id}-kfs"))

        evidence.append(build_evidence(f"doc-{ctx.loan_id}-kfs", "KFS.pdf", "KFS — Terms & Consent", 1))

        has_mismatch = any(r.get("match_status") == "MISMATCH" for r in kfs_records)
        core_kfs = [r for r in [r7_amt, r7_val] if r is not None]
        has_partial = any(r.get("match_status") in ("PARTIAL", "NOT_FOUND") for r in core_kfs)

        if has_mismatch:
            status = "DISCREPANCY"
            first_mis = next(r for r in kfs_records if r.get("match_status") == "MISMATCH")
            notes = first_mis.get("notes") or f"KFS discrepancy detected in {first_mis.get('field', 'terms')}."
            vals = first_mis.get("values") or []
            srcs = first_mis.get("sources") or []
            fld_name = first_mis.get("field", "")
            if fld_name == "irr_percent":
                v0 = f"{safe_float(vals[0]):.1f}%" if len(vals) > 0 and vals[0] is not None else ""
                v1 = f"{safe_float(vals[1]):.1f}%" if len(vals) > 1 and vals[1] is not None else ""
            elif fld_name in ("loan_amount", "funding_amount", "emi"):
                v0 = inr_format(vals[0]) if len(vals) > 0 and vals[0] is not None else ""
                v1 = inr_format(vals[1]) if len(vals) > 1 and vals[1] is not None else ""
            else:
                v0 = str(vals[0]) if len(vals) > 0 else ""
                v1 = str(vals[1]) if len(vals) > 1 else ""

            val_block = resolve_checkpoint_validation(
                status,
                default_left=v0,
                default_right=v1,
                records=None,
                default_left_source=srcs[0] if len(srcs) > 0 else "kfs",
                default_right_source=srcs[1] if len(srcs) > 1 else "los",
            )
        elif has_partial:
            status = "INDETERMINATE"
            notes = "KFS terms pending verification."
            val_block = resolve_checkpoint_validation(
                status,
                default_left=inr_format(ctx.loan_amount) if ctx.loan_amount > 0 else "N/A",
                default_right=inr_format(kfs_doc.get("loan_amount") or 0.0),
                records=kfs_records,
                default_left_source="los",
                default_right_source="kfs",
            )
        else:
            status = "VERIFIED"
            notes = (r7_amt.get("notes") if r7_amt else "") or (
                f"Key Fact Statement verified: amount {inr_format(ctx.loan_amount)}, consent intact."
            )
            val_block = resolve_checkpoint_validation(
                status,
                default_left=inr_format(ctx.loan_amount) if ctx.loan_amount > 0 else "N/A",
                default_right=inr_format(kfs_doc.get("loan_amount") or 0.0),
                records=kfs_records,
                default_left_source="los",
                default_right_source="kfs",
            )
    else:
        fields = [build_field("KFS", "Not Uploaded", 0.0, f"doc-{ctx.loan_id}")]
        status = "INDETERMINATE"
        notes = "KFS not uploaded."
        val_block = resolve_checkpoint_validation(
            status,
            default_left="Missing",
            default_right="Mandatory",
            records=None,
            default_left_source="kfs",
            default_right_source="los",
        )

    conf = compute_checkpoint_confidence(fields, kfs_records if kfs_records else None) if has_kfs else 0.0

    return build_checkpoint(
        7,
        "KFS",
        status,
        conf,
        notes,
        "KFS funding amount, interest rate (IRR), EMI, and customer consent must match LOS.",
        fields,
        evidence,
        val_block,
        comparisons=all_kfs_records,
    )


def build_sanction_letter_checkpoint(ctx: CaseContext) -> dict[str, Any]:
    """CP 8: Sanction Letter terms, amount, tenure, IRR, and EMI validation against LOS."""
    r8_amt = ctx.get_check_record("chk_check_financial_sanction_letter_loan_amount_vs_los", "chk_sanction_vs_los_funding")
    r8_name = ctx.get_check_record("chk_check_financial_sanction_letter_applicant_name_vs_los")
    r8_val = ctx.get_check_record("chk_check_financial_sanction_letter_loan_validity_vs_los")
    r8_irr = ctx.get_check_record("chk_check_financial_sanction_letter_irr_percent_vs_los")
    r8_emi = ctx.get_check_record("chk_check_financial_sanction_letter_emi_vs_los")

    sanction_records = [r for r in [r8_amt, r8_name, r8_val, r8_irr, r8_emi] if r is not None]
    all_sanction_records = [
        r for r in ctx.records
        if "sanction" in (r.get("sources") or [])
        or "sanction_letter" in (r.get("sources") or [])
        or (r.get("check_id") and "sanction" in r.get("check_id", "").lower())
    ]

    sanction_doc = ctx.get_doc("sanction_letter", "sanction")
    has_sanction = sanction_doc.get("loan_amount") is not None or ctx.has_doc_matching("sanction")

    fields: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    if has_sanction:
        sanc_raw_amt = sanction_doc.get("loan_amount")
        sanc_amt_val = safe_float(sanc_raw_amt) if sanc_raw_amt is not None else None
        sanc_amt_str = inr_format(sanc_amt_val) if sanc_amt_val is not None else "N/A"
        if sanc_amt_val is not None:
            amt_conf = resolve_field_confidence(doc=sanction_doc, field_name="loan_amount", record=r8_amt)
            fields.append(build_field("Sanction Amount", sanc_amt_str, amt_conf, f"doc-{ctx.loan_id}-sanction"))
        if sanction_doc.get("loan_validity") is not None:
            val_conf = resolve_field_confidence(doc=sanction_doc, field_name="loan_validity", record=r8_val)
            fields.append(build_field("Sanction Tenure", format_tenure_months(sanction_doc["loan_validity"]), val_conf, f"doc-{ctx.loan_id}-sanction"))
        if sanction_doc.get("irr_percent") is not None:
            irr_conf = resolve_field_confidence(doc=sanction_doc, field_name="irr_percent", record=r8_irr)
            fields.append(build_field("Sanction IRR", f"{safe_float(sanction_doc['irr_percent']):.1f}%", irr_conf, f"doc-{ctx.loan_id}-sanction"))
        if sanction_doc.get("emi") is not None:
            emi_conf = resolve_field_confidence(doc=sanction_doc, field_name="emi", record=r8_emi)
            fields.append(build_field("Sanction EMI", inr_format(sanction_doc["emi"]), emi_conf, f"doc-{ctx.loan_id}-sanction"))
        if "customer_consent" in sanction_doc and sanction_doc["customer_consent"] is not None:
            r8_consent = ctx.get_check_record("chk_check_financial_sanction_letter_customer_consent_vs_los")
            consent_val = "Verified (Consented)" if bool(sanction_doc["customer_consent"]) else "Missing / Not Consented"
            consent_conf = resolve_field_confidence(doc=sanction_doc, field_name="customer_consent", record=r8_consent) if bool(sanction_doc["customer_consent"]) else 0.0
            fields.append(build_field("Customer Consent", consent_val, consent_conf, f"doc-{ctx.loan_id}-sanction"))

        evidence.append(build_evidence(f"doc-{ctx.loan_id}-sanction", "Sanction_Letter.pdf", "Sanction Letter — Terms", 1))

        # Check for direct LOS mismatches (e.g. IRR 23% vs LOS 17%, or EMI 38200 vs LOS 35652)
        los_irr = ctx.los_data.get("irr_percent")
        doc_irr = sanction_doc.get("irr_percent")
        direct_irr_mismatch = bool(
            doc_irr is not None and los_irr is not None and abs(safe_float(doc_irr) - safe_float(los_irr)) >= 0.01
        )

        los_emi = ctx.los_data.get("emi")
        doc_emi = sanction_doc.get("emi")
        direct_emi_mismatch = bool(
            doc_emi is not None and los_emi is not None and abs(safe_float(doc_emi) - safe_float(los_emi)) >= 1.0
        )

        has_record_mismatch = any(r.get("match_status") == "MISMATCH" for r in sanction_records)
        has_mismatch = has_record_mismatch or direct_irr_mismatch or direct_emi_mismatch
        core_sanction = [r for r in [r8_amt, r8_val] if r is not None]
        has_partial = any(r.get("match_status") in ("PARTIAL", "NOT_FOUND") for r in core_sanction)

        left_src = "sanction_letter"
        right_src = "los"

        if has_mismatch:
            status = "DISCREPANCY"
            if direct_irr_mismatch:
                notes = f"Sanction Letter IRR discrepancy: {safe_float(doc_irr):.1f}% vs LOS {safe_float(los_irr):.1f}%."
                val_left = f"{safe_float(doc_irr):.1f}%"
                val_right = f"{safe_float(los_irr):.1f}%"
            elif direct_emi_mismatch:
                notes = f"Sanction Letter EMI discrepancy: {inr_format(doc_emi)} vs LOS {inr_format(los_emi)}."
                val_left = inr_format(doc_emi)
                val_right = inr_format(los_emi)
            elif has_record_mismatch:
                first_mis = next(r for r in sanction_records if r.get("match_status") == "MISMATCH")
                notes = first_mis.get("notes") or f"Sanction Letter discrepancy detected in {first_mis.get('field', 'terms')}."
                vals = first_mis.get("values") or []
                srcs = first_mis.get("sources") or []
                val_left = str(vals[0]) if len(vals) > 0 else inr_format(ctx.loan_amount)
                val_right = str(vals[1]) if len(vals) > 1 else sanc_amt_str
                if len(srcs) > 0:
                    left_src = srcs[0]
                if len(srcs) > 1:
                    right_src = srcs[1]
            else:
                notes = "Sanction Letter terms discrepancy detected against LOS."
                val_left = sanc_amt_str
                val_right = inr_format(ctx.loan_amount)
            val_block = resolve_checkpoint_validation(
                status,
                default_left=val_left,
                default_right=val_right,
                records=None,
                default_left_source=left_src,
                default_right_source=right_src,
            )
        elif has_partial:
            status = "INDETERMINATE"
            notes = "Sanction letter terms pending manual verification."
            val_block = resolve_checkpoint_validation(
                status,
                default_left=sanc_amt_str,
                default_right=inr_format(ctx.loan_amount),
                records=sanction_records,
                default_left_source="sanction_letter",
                default_right_source="los",
            )
        else:
            status = "VERIFIED"
            notes = (r8_amt.get("notes") if r8_amt else "") or (
                f"Sanction letter matches approved loan amount {inr_format(ctx.loan_amount)}."
            )
            val_block = resolve_checkpoint_validation(
                status,
                default_left=sanc_amt_str,
                default_right=inr_format(ctx.loan_amount),
                records=sanction_records,
                default_left_source="sanction_letter",
                default_right_source="los",
            )
    else:
        fields = [build_field("Sanction Letter", "Not Uploaded", 0.0, f"doc-{ctx.loan_id}")]
        status = "INDETERMINATE"
        notes = "Sanction letter not uploaded."
        val_block = resolve_checkpoint_validation(
            status,
            default_left="Missing",
            default_right="Mandatory",
            records=None,
            default_left_source="sanction_letter",
            default_right_source="los",
        )

    conf = compute_checkpoint_confidence(fields, sanction_records if sanction_records else None) if has_sanction else 0.0

    return build_checkpoint(
        8,
        "Sanction Letter",
        status,
        conf,
        notes,
        "Sanction Letter terms (amount, tenure, IRR, EMI) must match approved terms in LOS.",
        fields,
        evidence,
        val_block,
        comparisons=all_sanction_records,
    )


def build_bpi_checkpoint(ctx: CaseContext) -> dict[str, Any]:
    """CP 10: Broken Period Interest (BPI) split consistency."""
    r10 = ctx.get_check_record(
        "chk_check_financial_kfs_bpi_vs_los",
        "chk_check_financial_kfs_vs_disbursal_memo_bpi_charge",
        "chk_broken_period_interest_split",
        field="bpi",
    )
    kfs_doc = ctx.get_doc("kfs")
    sanction_doc = ctx.get_doc("sanction_letter", "sanction")
    memo_doc = ctx.get_doc("disbursal_memo", "memo")

    bpi_records = [
        r for r in ctx.records
        if "bpi" in (r.get("field") or "").lower()
        or (r.get("check_id") and "bpi" in r.get("check_id", "").lower())
    ]

    bpi_val = (
        kfs_doc.get("BPI")
        or kfs_doc.get("bpi")
        or kfs_doc.get("broken_period_interest")
        or kfs_doc.get("bpi_charge")
        or sanction_doc.get("broken_period_interest")
        or memo_doc.get("bpi_charge")
    )
    los_bpi = ctx.los_data.get("bpi_charges") or ctx.los_data.get("bpi")
    has_bpi = bpi_val is not None

    fields: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    if has_bpi:
        bpi_conf = resolve_field_confidence(doc=kfs_doc, field_name="bpi", record=r10)
        fields = [build_field("BPI Value", inr_format(bpi_val), bpi_conf, f"doc-{ctx.loan_id}-kfs")]
        evidence = [build_evidence(f"doc-{ctx.loan_id}-kfs", "KFS.pdf", "KFS — BPI", 1)]
        bpi_match = (safe_float(los_bpi) == safe_float(bpi_val)) if los_bpi is not None else True
        if (r10 and r10.get("match_status") == "MISMATCH") or not bpi_match:
            status = "DISCREPANCY"
        elif r10 and r10.get("match_status") in ("PARTIAL", "NOT_FOUND"):
            status = "INDETERMINATE"
        else:
            status = "VERIFIED"
        right_bpi_str = inr_format(los_bpi) if los_bpi is not None else inr_format(bpi_val)
        notes = (r10.get("notes") if r10 else "") or f"Broken Period Interest split of {inr_format(bpi_val)} verified against records."
        val_block = resolve_checkpoint_validation(
            status,
            default_left=inr_format(bpi_val),
            default_right=right_bpi_str,
            records=bpi_records,
            default_left_source="kfs",
            default_right_source="los",
        )
    else:
        fields = [build_field("BPI", "Not Available", 0.0, f"doc-{ctx.loan_id}")]
        status = "NOT_APPLICABLE"
        notes = "Broken Period Interest not applicable or not provided."
        val_block = resolve_checkpoint_validation(
            status,
            default_left="Not Available",
            default_right="Not Applicable",
            records=None,
            default_left_source="kfs",
            default_right_source="los",
        )

    conf = compute_checkpoint_confidence(fields, bpi_records if bpi_records else ([r10] if r10 else None)) if has_bpi else 0.0

    return build_checkpoint(
        10,
        "BPI",
        status,
        conf,
        notes,
        "Broken period interest split must be consistent across documents and LOS.",
        fields,
        evidence,
        val_block,
        comparisons=bpi_records,
    )


def build_disbursal_memo_checkpoint(ctx: CaseContext) -> dict[str, Any]:
    """CP 11: Disbursal Memo threshold, loan ID, and application alignment."""
    r11_amt = ctx.get_check_record("chk_check_financial_disbursal_memo_loan_amount_vs_los", "chk_disbursal_memo_amount_threshold")
    r11_no = ctx.get_check_record(
        "chk_check_financial_disbursal_memo_loan_no_vs_los",
        "chk_check_loan_application_disbursal_memo_loan_no_vs_los",
    )
    r11_acct_no = ctx.get_check_record("chk_check_financial_account_statement_account_no_vs_los")
    memo_doc = ctx.get_doc("disbursal_memo", "memo")
    acct_doc = ctx.get_doc("account_statement", "acctstmt")
    has_memo = bool(memo_doc) or ctx.has_doc_matching("memo", "disbursal")

    memo_records = [
        r for r in ctx.records
        if "disbursal_memo" in (r.get("sources") or [])
        or (r.get("check_id") and "disbursal_memo" in r.get("check_id", "").lower())
    ]

    fields: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    if has_memo:
        status = "VERIFIED"
        if (
            (r11_amt and r11_amt.get("match_status") == "MISMATCH")
            or (r11_no and r11_no.get("match_status") == "MISMATCH")
            or (r11_acct_no and r11_acct_no.get("match_status") == "MISMATCH")
        ):
            status = "DISCREPANCY"
        elif (
            (r11_amt and r11_amt.get("match_status") in ("PARTIAL", "NOT_FOUND"))
            or (r11_no and r11_no.get("match_status") in ("PARTIAL", "NOT_FOUND"))
        ):
            status = "INDETERMINATE"

        raw_disbursal = memo_doc.get("disbursal_amount") or memo_doc.get("loan_amount")
        memo_app_id = memo_doc.get("application_id")
        conf_amt = resolve_field_confidence(doc=memo_doc, field_name="disbursal_amount", record=r11_amt) if raw_disbursal is not None else 0.0
        conf_app_id = resolve_field_confidence(doc=memo_doc, field_name="application_id", record=r11_no) if memo_app_id else 0.0
        fields = [
            build_field("Disbursal Amount", inr_format(safe_float(raw_disbursal)) if raw_disbursal is not None else None, conf_amt, f"doc-{ctx.loan_id}-disbursalmemo"),
            build_field("Application ID", str(memo_app_id) if memo_app_id else None, conf_app_id, f"doc-{ctx.loan_id}-disbursalmemo"),
        ]
        if memo_doc.get("loan_no"):
            conf_loan_no = resolve_field_confidence(doc=memo_doc, field_name="loan_no", record=r11_no)
            fields.append(build_field("Loan Number", str(memo_doc["loan_no"]), conf_loan_no, f"doc-{ctx.loan_id}-disbursalmemo"))
        if acct_doc and (acct_doc.get("account_number") or acct_doc.get("account_no")):
            conf_acct = resolve_field_confidence(doc=acct_doc, field_name="account_no", record=r11_acct_no)
            fields.append(build_field("Bank Account No", str(acct_doc.get("account_number") or acct_doc.get("account_no")), conf_acct, f"doc-{ctx.loan_id}-acctstmt"))

        evidence = [build_evidence(f"doc-{ctx.loan_id}-disbursalmemo", "Disbursal_Memo.pdf", "Disbursal Memo", 1)]
        if acct_doc:
            evidence.append(build_evidence(f"doc-{ctx.loan_id}-acctstmt", "Account_Statement.pdf", "Account Statement — Bank Details", 1, "Account Number"))
        notes = (r11_amt.get("notes") if r11_amt else "") or (
            f"Disbursal memo amount {inr_format(ctx.disbursal_amount)} meets threshold."
        )
        val_block = resolve_checkpoint_validation(
            status,
            default_left=inr_format(ctx.disbursal_amount),
            default_right=f">= {inr_format(ctx.loan_amount * 0.9)}" if ctx.loan_amount > 0 else "N/A",
            records=memo_records,
            default_left_source="disbursal_memo",
            default_right_source="los",
        )
    else:
        fields = [build_field("Disbursal Memo", "Not Uploaded", 0.0, f"doc-{ctx.loan_id}")]
        status = "INDETERMINATE"
        notes = "Disbursal memo not uploaded."
        val_block = resolve_checkpoint_validation(
            status,
            default_left="Missing",
            default_right=f">= {inr_format(ctx.loan_amount * 0.9)}" if ctx.loan_amount > 0 else "Mandatory",
            records=None,
            default_left_source="disbursal_memo",
            default_right_source="los",
            fallback_result="MISMATCH",
        )

    conf = compute_checkpoint_confidence(fields, memo_records if memo_records else None) if has_memo else 0.0

    return build_checkpoint(
        11,
        "Disbursal Memo",
        status,
        conf,
        notes,
        "Disbursal Memo amount must be at least 90% of approved loan amount, and loan ID must match.",
        fields,
        evidence,
        val_block,
        comparisons=memo_records,
    )
