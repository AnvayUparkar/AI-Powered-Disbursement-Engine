import re

# Regex patterns for sensitive financial and KYC PII
AADHAAR_PATTERN = re.compile(r"\b(\d{4})[\s\-]?(\d{4})[\s\-]?(\d{4})\b")
PAN_PATTERN = re.compile(r"\b([A-Z]{5})(\d{4})([A-Z]{1})\b")
ACCOUNT_PATTERN = re.compile(r"\b\d{10,18}\b")


def mask_sensitive_pii(text: str) -> str:
    """
    Masks sensitive financial and KYC PII (Aadhaar, PAN, Bank Account numbers)
    in log messages and production audit outputs.
    """
    if not text:
        return ""

    masked = text

    # Mask Aadhaar numbers -> XXXX-XXXX-1234
    def _mask_aadhaar(match):
        last4 = match.group(3)
        return f"XXXX-XXXX-{last4}"

    masked = AADHAAR_PATTERN.sub(_mask_aadhaar, masked)

    # Mask PAN numbers -> XXXXX1234X
    def _mask_pan(match):
        digits = match.group(2)
        last_char = match.group(3)
        return f"XXXXX{digits}{last_char}"

    masked = PAN_PATTERN.sub(_mask_pan, masked)

    # Mask Bank Account numbers -> XXXXXX1234
    def _mask_account(match):
        val = match.group(0)
        if len(val) >= 10:
            return "X" * (len(val) - 4) + val[-4:]
        return val

    masked = ACCOUNT_PATTERN.sub(_mask_account, masked)

    return masked
