import logging
import re
from typing import Any

import jellyfish
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from pipeline.config import (
    ADDRESS_MATCH_ALGO,
    FUZZY_MATCH_BAND,
    FUZZY_PARTIAL_LOWER,
    NAME_MATCH_ALGO,
)
from pipeline.nodes.llm_adjudicator import llm_adjudicate
from pipeline.state import PipelineState, compute_rollup

logger = logging.getLogger("disbursement_pipeline.node3a")


def _clean_numeric(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val)
    # Remove currency symbols, commas, spaces
    cleaned = re.sub(r"[^\d.]", "", val_str)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalize_tenure_months(val: Any) -> int | None:
    if val is None:
        return None
    if isinstance(val, int):
        return val
    val_str = str(val).strip().lower()
    match = re.search(r"(\d+)", val_str)
    if not match:
        return None
    num = int(match.group(1))
    if "year" in val_str or "yr" in val_str:
        return num * 12
    return num


def _clean_id(val: Any) -> str | None:
    if val is None:
        return None
    return re.sub(r"\s+", "", str(val)).upper()


def _compute_tfidf_cosine(text1: str, text2: str) -> float:
    t1 = text1.strip()
    t2 = text2.strip()
    if not t1 or not t2:
        return 0.0
    if t1.lower() == t2.lower():
        return 1.0
    try:
        vectorizer = TfidfVectorizer().fit([t1, t2])
        vectors = vectorizer.transform([t1, t2])
        sim = cosine_similarity(vectors[0:1], vectors[1:2])[0][0]
        return float(sim)
    except (ValueError, TypeError) as e:
        logger.warning("TF-IDF cosine computation failed: %s", e)
        return 0.0


def node3a_loan_kyc(state: PipelineState) -> dict:
    """Node 3a — Loan & KYC Checks

    - Loan amount consistency across application, agreement, KFS, sanction
    - Loan validity / tenure comparison
    - Application form name match (Jaro-Winkler)
    - KYC PAN exact ID match
    - KYC Address proof match (TF-IDF cosine)
    """
    loan_id = state["loan_id"]
    extracted = state.get("extracted_data", {})
    los = state.get("los_data", {})
    records: list[dict] = []

    logger.info("Executing Node 3a (Loan & KYC) for loan %s", loan_id)

    # 1. Loan Amount Consistency (Application, Agreement, KFS, Sanction)
    app_doc = extracted.get("application_form", {})
    agree_doc = extracted.get("loan_agreement", {})
    kfs_doc = extracted.get("kfs", {})
    sanction_doc = extracted.get("sanction_letter", {})

    app_amt = _clean_numeric(app_doc.get("loan_amount"))
    agree_amt = _clean_numeric(agree_doc.get("loan_amount"))
    kfs_amt = _clean_numeric(kfs_doc.get("loan_amount"))
    sanction_amt = _clean_numeric(sanction_doc.get("loan_amount"))

    sources_map = {
        "application_form": app_amt,
        "loan_agreement": agree_amt,
        "kfs": kfs_amt,
        "sanction_letter": sanction_amt,
    }

    pairs = [
        ("application_form", "loan_agreement"),
        ("application_form", "kfs"),
        ("application_form", "sanction_letter"),
        ("loan_agreement", "kfs"),
        ("loan_agreement", "sanction_letter"),
        ("kfs", "sanction_letter"),
    ]

    for s1, s2 in pairs:
        v1, v2 = sources_map[s1], sources_map[s2]
        check_id = f"chk_loan_amt_{s1}_vs_{s2}"
        if v1 is None or v2 is None:
            records.append({
                "check_id": check_id,
                "subnode": "loan_kyc",
                "field": "loan_amount",
                "sources": [s1, s2],
                "values": [v1, v2],
                "match_type": "exact_numeric",
                "match_status": "NOT_FOUND",
                "confidence": 0.0,
                "method": "zero_tolerance_numeric_compare",
                "llm_used": False,
                "notes": f"Missing amount in {s1 if v1 is None else s2}",
            })
        else:
            is_match = (v1 == v2)
            records.append({
                "check_id": check_id,
                "subnode": "loan_kyc",
                "field": "loan_amount",
                "sources": [s1, s2],
                "values": [v1, v2],
                "match_type": "exact_numeric",
                "match_status": "MATCH" if is_match else "MISMATCH",
                "confidence": 1.0,
                "method": "zero_tolerance_numeric_compare",
                "llm_used": False,
                "notes": None if is_match else f"Amount mismatch: {v1} vs {v2}",
            })

    # 2. Loan Validity / Tenure Match (Sanction / Agreement vs Application)
    app_tenure = _normalize_tenure_months(app_doc.get("tenure_months") or app_doc.get("tenure"))
    sanction_tenure = _normalize_tenure_months(sanction_doc.get("tenure_months") or sanction_doc.get("tenure"))

    check_id = "chk_loan_validity_tenure"
    if app_tenure is None or sanction_tenure is None:
        records.append({
            "check_id": check_id,
            "subnode": "loan_kyc",
            "field": "tenure_months",
            "sources": ["application_form", "sanction_letter"],
            "values": [app_tenure, sanction_tenure],
            "match_type": "exact",
            "match_status": "NOT_FOUND",
            "confidence": 0.0,
            "method": "normalized_tenure_compare",
            "llm_used": False,
            "notes": "Tenure missing in application or sanction letter",
        })
    else:
        is_match = (app_tenure == sanction_tenure)
        records.append({
            "check_id": check_id,
            "subnode": "loan_kyc",
            "field": "tenure_months",
            "sources": ["application_form", "sanction_letter"],
            "values": [app_tenure, sanction_tenure],
            "match_type": "exact",
            "match_status": "MATCH" if is_match else "MISMATCH",
            "confidence": 1.0,
            "method": "normalized_tenure_compare",
            "llm_used": False,
            "notes": None if is_match else f"Tenure mismatch: {app_tenure}m vs {sanction_tenure}m",
        })

    # 3. Application Form Match (Applicant Name vs LOS)
    app_name = str(app_doc.get("applicant_name") or app_doc.get("name") or "").strip()
    los_name = str(los.get("applicant_name") or los.get("name") or "").strip()

    check_id = "chk_app_form_name_match"
    if not app_name or not los_name:
        records.append({
            "check_id": check_id,
            "subnode": "loan_kyc",
            "field": "applicant_name",
            "sources": ["application_form", "los"],
            "values": [app_name or None, los_name or None],
            "match_type": "fuzzy",
            "match_status": "NOT_FOUND",
            "confidence": 0.0,
            "method": NAME_MATCH_ALGO,
            "llm_used": False,
            "notes": "Applicant name missing in application or LOS",
        })
    else:
        score = jellyfish.jaro_winkler_similarity(app_name.lower(), los_name.lower())
        if score >= FUZZY_MATCH_BAND:
            status = "MATCH"
        elif score >= FUZZY_PARTIAL_LOWER:
            status = "PARTIAL"
        else:
            status = "MISMATCH"

        records.append({
            "check_id": check_id,
            "subnode": "loan_kyc",
            "field": "applicant_name",
            "sources": ["application_form", "los"],
            "values": [app_name, los_name],
            "match_type": "fuzzy",
            "match_status": status,
            "confidence": round(score, 4),
            "method": NAME_MATCH_ALGO,
            "llm_used": False,
            "notes": None if status == "MATCH" else f"Similarity: {score:.4f}",
        })

    # 4. KYC — PAN Check (KYC PAN vs Application Form)
    pan_doc = extracted.get("kyc_pan", {})
    pan_kyc = _clean_id(pan_doc.get("pan_number") or pan_doc.get("pan"))
    pan_app = _clean_id(app_doc.get("pan_number") or app_doc.get("pan"))

    check_id = "chk_kyc_pan"
    if not pan_kyc or not pan_app:
        records.append({
            "check_id": check_id,
            "subnode": "loan_kyc",
            "field": "pan_number",
            "sources": ["kyc_pan", "application_form"],
            "values": [pan_kyc, pan_app],
            "match_type": "exact_id",
            "match_status": "NOT_FOUND",
            "confidence": 0.0,
            "method": "strip_spaces_equality",
            "llm_used": False,
            "notes": "PAN missing in KYC document or application form (Mandatory)",
        })
    else:
        is_match = (pan_kyc == pan_app)
        records.append({
            "check_id": check_id,
            "subnode": "loan_kyc",
            "field": "pan_number",
            "sources": ["kyc_pan", "application_form"],
            "values": [pan_kyc, pan_app],
            "match_type": "exact_id",
            "match_status": "MATCH" if is_match else "MISMATCH",
            "confidence": 1.0,
            "method": "strip_spaces_equality",
            "llm_used": False,
            "notes": None if is_match else f"PAN mismatch: {pan_kyc} vs {pan_app}",
        })

    # 5. KYC — Address Proof Match (Address doc vs Application / Aadhaar)
    addr_doc = extracted.get("kyc_address_proof", {})
    addr_proof = str(addr_doc.get("address_text") or addr_doc.get("address") or "").strip()
    addr_app = str(app_doc.get("address_text") or app_doc.get("address") or "").strip()

    check_id = "chk_kyc_address_proof"
    if not addr_proof or not addr_app:
        records.append({
            "check_id": check_id,
            "subnode": "loan_kyc",
            "field": "address",
            "sources": ["kyc_address_proof", "application_form"],
            "values": [addr_proof or None, addr_app or None],
            "match_type": "fuzzy",
            "match_status": "NOT_FOUND",
            "confidence": 0.0,
            "method": ADDRESS_MATCH_ALGO,
            "llm_used": False,
            "notes": "Address missing in address proof or application (Mandatory)",
        })
    else:
        sim = _compute_tfidf_cosine(addr_proof, addr_app)
        if sim >= FUZZY_MATCH_BAND:
            status = "MATCH"
        elif sim >= FUZZY_PARTIAL_LOWER:
            status = "PARTIAL"
        else:
            status = "MISMATCH"

        records.append({
            "check_id": check_id,
            "subnode": "loan_kyc",
            "field": "address",
            "sources": ["kyc_address_proof", "application_form"],
            "values": [addr_proof, addr_app],
            "match_type": "fuzzy",
            "match_status": status,
            "confidence": round(sim, 4),
            "method": ADDRESS_MATCH_ALGO,
            "llm_used": False,
            "notes": None if status == "MATCH" else f"TF-IDF cosine similarity: {sim:.4f}",
        })

    # Conditional Routing: LLM Adjudication for PARTIAL fuzzy matches
    for record in records:
        if record["match_type"] == "fuzzy" and record["match_status"] == "PARTIAL":
            val_a, val_b = record["values"][0], record["values"][1]
            adjudication = llm_adjudicate(val_a, val_b, record["field"], loan_id)
            record["match_status"] = adjudication["match_status"]
            record["llm_used"] = adjudication["llm_used"]
            if adjudication.get("confidence") is not None:
                record["confidence"] = adjudication["confidence"]
            
            note_parts = [record.get("notes"), f"Adjudication: {adjudication['reason']}"]
            record["notes"] = " | ".join(p for p in note_parts if p)

    rollup = compute_rollup(records)
    logger.info("Node 3a completed with rollup: %s (%d records)", rollup, len(records))

    return {
        "records": records,
        "rollup": rollup,
    }

