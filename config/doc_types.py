"""Document types definitions, canonical aliases, and detection utilities."""
import re
from pathlib import Path
from typing import Optional

# Canonical document key mapping and aliases
DOC_TYPE_ALIASES: dict[str, list[str]] = {
    "aadhaar": ["aadhaar", "kyc_address_proof", "aadhaar_card", "aadhar"],
    "aadhaar_xml": ["aadhaar_xml", "aadhaarxml", "xml_aadhaar"],
    "pan": ["pan", "kyc_pan", "pan_card"],
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

# Canonical supported document file extensions across ingestion, IDP, and UI
SUPPORTED_DOCUMENT_EXTENSIONS: set[str] = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tiff",
    ".tif",
    ".bmp",
    ".xml",
    ".zip",
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

    stem = Path(name_or_key).stem.lower().strip()
    # Direct match in alias map
    if stem in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[stem]

    # Strip leading loan_id prefix if present (e.g., 'appl00343265_kfs' -> 'kfs', 'loan_001_pan' -> 'pan')
    unprefixed = re.sub(r"^(?:appl\d+|loan_\d+|[a-z0-9]+)_(.+)$", r"\1", stem)
    if unprefixed in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[unprefixed]

    # Also try space-normalised stem (e.g. "key fact statement" -> "key_fact_statement")
    stem_underscored = stem.replace(" ", "_")
    if stem_underscored in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[stem_underscored]
    # Substring heuristics — prioritize distinctive keywords over short prefixes
    if "xml" in stem and ("aadhaar" in stem or "aadhar" in stem):
        return "aadhaar_xml"
    if "aadhaar" in stem or "aadhar" in stem:
        return "aadhaar"
    if "kfs" in stem or ("key" in stem and "fact" in stem):
        return "kfs"
    if "sanction" in stem:
        return "sanction_letter"
    if "agreement" in stem:
        return "loan_agreement"
    if "memo" in stem or "disbursal" in stem:
        return "disbursal_memo"
    if "statement" in stem or "bank" in stem:
        return "account_statement"
    if "vkyc" in stem:
        return "vkyc"
    if "bt" in stem or "foreclosure" in stem:
        return "bt_details"
    if "pan" in stem:
        return "pan"
    if "application" in stem or re.search(r"(?:^|_)app(?:lication)?(?:_|$)", unprefixed):
        return "application_form"

    return stem


def get_display_name(doc_type: str) -> str:
    """Returns the user-facing display name for a document type."""
    canonical = get_canonical_doc_type(doc_type)
    return DOC_TYPE_DISPLAY_NAMES.get(canonical, canonical.replace("_", " ").title())


TABULAR_OR_MISC_DOCS = frozenset({
    "application_form",
    "kfs",
    "sanction_letter",
    "account_statement",
    "loan_agreement",
    "disbursal_memo",
    "miscellaneous",
})


def is_tabular_or_misc_doc(doc_type: str) -> bool:
    """Returns True if the document type requires TableFormer table structure detection.

    Pure identity cards (Aadhaar, PAN, voter ID, passport, driving license) do not contain
    financial tables and bypass TableFormer, saving ~45s/page of CPU transformer inference.
    All tabular, financial, and miscellaneous/unmapped document types execute TableFormer.
    """
    canonical = get_canonical_doc_type(doc_type).lower()
    if canonical in {"aadhaar", "pan", "voter_id", "passport", "driving_license"}:
        return False
    return True


# Ordered canonical field names strictly matching the required JSON template schema.
TEMPLATE_FIELDS: tuple[str, ...] = (
    "applicant_name",
    "fathers_name",
    "dob",
    "mobile_no",
    "gender",
    "aadhaar_number",
    "pan_number",
    "address",
    "current_address",
    "bank_account_no",
    "type_of_account",
    "loan_amount",
    "loan_validity",
    "loan_type",
    "application_no",
    "application_date",
    "BPI",
    "irr_percent",
    "emi",
    "customer_consent",
)

_CANONICAL_KEYS: frozenset[str] = frozenset(TEMPLATE_FIELDS)


def format_template_json(extracted: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Formats an arbitrary extracted dictionary into the exact 20-field canonical template.

    Keys are returned in the exact canonical order with non-present fields as None,
    and boolean flag (customer_consent) as False by default.
    """
    boolean_keys = {"customer_consent"}
    norm = dict(extracted or {})

    if norm.get("applicant_name") is None:
        for alias in ("customer_name", "borrower_name", "full_name", "name"):
            if norm.get(alias) is not None:
                norm["applicant_name"] = norm[alias]
                break
    if norm.get("bank_account_no") is None and "account_no" in norm:
        norm["bank_account_no"] = norm["account_no"]
    if norm.get("application_no") is None:
        for alias in ("loan_no", "loan_account_no", "application_id", "loan_id", "appl_no", "los_id"):
            if norm.get(alias) is not None:
                norm["application_no"] = norm[alias]
                break
    if norm.get("pan_number") is None and norm.get("pan") is not None:
        norm["pan_number"] = norm["pan"]
    if norm.get("aadhaar_number") is None and norm.get("aadhaar") is not None:
        norm["aadhaar_number"] = norm["aadhaar"]
    if norm.get("loan_amount") is None:
        for alias in ("sanctioned_amount", "funding_amount", "disbursal_amount", "requested_loan_amount"):
            if norm.get(alias) is not None:
                norm["loan_amount"] = norm[alias]
                break
    if norm.get("loan_validity") is None:
        for alias in ("tenure_months", "tenure", "tenor", "tenure_of_loan"):
            if norm.get(alias) is not None:
                norm["loan_validity"] = norm[alias]
                break
    if norm.get("loan_type") is None:
        for alias in ("type_of_loan", "end_use", "purpose_of_loan"):
            if norm.get(alias) is not None:
                norm["loan_type"] = norm[alias]
                break
    if norm.get("BPI") is None:
        for alias in ("bpi", "broken_period_interest"):
            if norm.get(alias) is not None:
                norm["BPI"] = norm[alias]
                break
    if norm.get("irr_percent") is None:
        for alias in ("roi", "interest_rate", "irr"):
            if norm.get(alias) is not None:
                norm["irr_percent"] = norm[alias]
                break
    if norm.get("customer_consent") is None:
        for alias in ("consent", "is_consented", "otp_consent", "borrower_consent", "customer_acceptance"):
            if norm.get(alias) is not None:
                norm["customer_consent"] = norm[alias]
                break
    if norm.get("address") is None and norm.get("address_text") is not None:
        norm["address"] = norm["address_text"]

    result: dict[str, Any] = {}
    for k in TEMPLATE_FIELDS:
        if k in boolean_keys:
            val = norm.get(k, False)
            result[k] = bool(val) if val is not None else False
        else:
            result[k] = norm.get(k, None)
    return result

