import re
from typing import List, Dict
from idp.services.table_processing.models import RawCell


class KeyValueDetector:
    """Detects and extracts key-value pairs from row cells in financial tables."""

    INDEX_PATTERN = re.compile(r"^(?:\d+|[a-zA-Z]|[ivxIVX]+|[a-zA-Z]\))\s*[\.\)]?$")

    @classmethod
    def is_index_cell(cls, text: str) -> bool:
        clean = text.strip()
        if not clean:
            return False
        return bool(cls.INDEX_PATTERN.match(clean)) or clean.lower() in {"sr.", "sr. no.", "sl no", "sl. no.", "no.", "no"}

    @classmethod
    def extract_pairs(cls, cells: List[RawCell]) -> List[Dict[str, str]]:
        """
        Extracts key-value pairs from a list of cells.
        Handles leading index cells (1, 2, a, i, etc.) and multiple key-value pairs across the row.
        """
        if not cells:
            return []

        active_cells = [c for c in cells if c.text and c.text.strip()]
        if not active_cells:
            return []

        start_idx = 0
        if cls.is_index_cell(active_cells[0].text):
            start_idx = 1

        remaining = active_cells[start_idx:]
        pairs: List[Dict[str, str]] = []

        idx = 0
        while idx + 1 < len(remaining):
            field_txt = remaining[idx].text.strip()
            val_txt = remaining[idx + 1].text.strip()
            if field_txt:
                pairs.append({"field": field_txt, "value": val_txt})
            idx += 2

        return pairs
