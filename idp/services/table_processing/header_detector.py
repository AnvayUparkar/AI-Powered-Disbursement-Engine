import re
from typing import List
from idp.services.table_processing.models import RawRow, RawCell


class HeaderDetector:
    """Detects multi-column structured table headers."""

    COMMON_HEADER_KEYWORDS = {
        "installment", "installments", "epi", "emi", "number of", "commencement",
        "date", "due date", "instl", "principal", "interest", "closing", "opening",
        "rate", "flag", "sr", "sr.", "sl", "no", "no.", "parameter", "details",
        "charges", "fee", "amount", "payable", "description", "particulars", "tenor",
        "balance", "outstanding", "bounce", "penalty", "gst", "recovery", "agent"
    }

    NUMERIC_PATTERN = re.compile(r"^[₹$€£\s]*\d+(?:,\d+)*(?:\.\d+)?%?\s*(?:/-)?$")
    COMMON_VALUE_TOKENS = {"fixed", "floating", "hybrid", "yes", "no", "na", "n/a", "nil", "monthly", "quarterly"}

    @classmethod
    def is_header(cls, row: RawRow) -> bool:
        """Determines if a row represents a structured column header."""
        if not row.cells or len(row.cells) < 2:
            return False

        texts = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
        if len(texts) < 2:
            return False

        # If any cell is purely numeric (like 28000.00, 35.00 %, 12), it's data, not a header
        for t in texts:
            if cls.NUMERIC_PATTERN.match(t):
                return False
            if t.lower() in cls.COMMON_VALUE_TOKENS:
                return False

        # A 2-cell row is typically key-value unless explicitly standard column headers
        if len(texts) == 2:
            t0_low = texts[0].lower()
            t1_low = texts[1].lower()
            if not ((t0_low in {"sr.", "no", "parameter", "field", "charge", "fee"} and t1_low in {"details", "value", "amount"})):
                return False

        # Count header keywords
        keyword_hits = 0
        for t in texts:
            t_low = t.lower()
            if any(k in t_low for k in cls.COMMON_HEADER_KEYWORDS):
                keyword_hits += 1

        if keyword_hits >= 1 and len(texts) >= 2:
            return True

        return False
