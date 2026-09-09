"""Document types definitions, canonical aliases, and detection utilities."""
import re
from pathlib import Path
from typing import Optional

# Canonical document key mapping and aliases
DOC_TYPE_ALIASES: dict[str, list[str]] = {
    "aadhaar": ["aadhaar", "kyc_address_proof", "aadhaar_card", "aadhar", "adhar", "adhar_card", "eaadhaar", "eaadhar", "uidai"],
    "aadhaar_xml": ["aadhaar_xml", "aadhaarxml", "xml_aadhaar", "aadhar_xml", "adhar_xml", "xml_aadhar"],
    "pan": ["pan", "kyc_pan", "pan_card", "pancard"],
    "application_form": ["application_form", "loan_application", "application", "appform", "app_form"],
    "account_statement": ["account_statement", "bank_statement", "bank_account_statement", "bank_statement_6m"],
    "kfs": ["kfs", "key_fact_statement", "key_fact_statement_kfs"],
    "sanction_letter": ["sanction_letter", "sanction"],
    "loan_agreement": ["loan_agreement", "agreement"],
    "disbursal_memo": ["disbursal_memo", "disbursement_memo", "memo", "disbursalmemo"],
    "vkyc": ["vkyc", "vkyc_audit_trail", "vkyc_audit"],
    "bt_details": ["bt_details", "foreclosure_letter", "bt"],
}

# Display name mappings for UI and reports
DOC_TYPE_DISPLAY_NAMES: dict[str, str] = {
    "aadhaar": "Aadhaar",
    "aadhaar_xml": "Aadhaar XML",
    "pan": "PAN Card",
    "application_form": "Application Form",
    "account_statement": "Bank Statement",
    "kfs": "Key Fact Statement (KFS)",
    "sanction_letter": "Sanction Letter",
    "loan_agreement": "Loan Agreement",
    "disbursal_memo": "Disbursal Memo",
    "vkyc": "VKYC Audit Trail",
    "bt_details": "BT Foreclosure Details",
}

# Reverse mapping: alias -> canonical key
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for canonical, aliases in DOC_TYPE_ALIASES.items():
    _ALIAS_TO_CANONICAL[canonical.lower()] = canonical
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = canonical


def get_canonical_doc_type(name_or_key: str) -> str:
    """Resolves any filename, path, or key to its canonical document key."""
    if not name_or_key:
        return "miscellaneous"

    p = Path(name_or_key)
    stem = p.stem.lower().strip()
    suffix = p.suffix.lower().strip()
    full = name_or_key.lower().strip()

    # Prioritize Aadhaar XML detection if XML extension or token is present
    if (suffix == ".xml" or "xml" in full) and any(k in full for k in ("aadhaar", "aadhar", "adhar", "uidai")):
        return "aadhaar_xml"

    # Direct match in alias map
    if stem in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[stem]

    # Strip leading loan_id prefix if present (e.g., 'appl00343265_kfs' -> 'kfs', 'loan_001_pan' -> 'pan')
    unprefixed = re.sub(r"^(?:appl\d+|loan_\d+|[a-z0-9]+)_(.+)$", r"\1", stem)
    if unprefixed in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[unprefixed]

    # Substring heuristics — prioritize distinctive keywords over short prefixes
    if any(k in stem for k in ("aadhaar", "aadhar", "adhar", "uidai")):
        return "aadhaar"
    if "kfs" in stem:
        return "kfs"
    if "sanction" in stem:
        return "sanction_letter"
    if "agreement" in stem:
        return "loan_agreement"
    if "memo" in stem or "disbursal" in stem:
        return "disbursal_memo"
    if "statement" in stem or "bank" in stem:
        return "account_statement"
    if "vkyc" in stem or "vky" in stem:
        return "vkyc"
    if bool(re.search(r"(?:^|[\W_])bt(?:[\W_]|$)", stem)) or "foreclosure" in stem or "balance_transfer" in stem:
        return "bt_details"
    if bool(re.search(r"(?:^|[\W_])pan(?:[\W_]|$)", stem)) or "pancard" in stem:
        return "pan"
    if "application" in stem or re.search(r"(?:^|[\W_])app(?:lication)?(?:[\W_]|$)", unprefixed):
        return "application_form"

    return stem


def get_display_name(doc_type: str) -> str:
    """Returns the user-facing display name for a document type."""
    canonical = get_canonical_doc_type(doc_type)
    return DOC_TYPE_DISPLAY_NAMES.get(canonical, canonical.replace("_", " ").title())
