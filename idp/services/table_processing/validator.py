import re
from typing import List
from idp.services.table_processing.models import LogicalBlock, LogicalBlockType


class TableValidator:
    """Validates structural consistency and data correctness in reconstructed tables."""

    NUMERIC_DATA_PATTERN = re.compile(r"^\d{4,}$")

    @classmethod
    def validate(cls, block: LogicalBlock) -> List[str]:
        """Validates a logical block and returns any quality warnings."""
        warnings: List[str] = []

        if block.block_type == LogicalBlockType.STRUCTURED_TABLE:
            if not block.headers and not block.rows:
                warnings.append(f"Block {block.id} has no headers or data rows.")

            for h in block.headers:
                h_str = str(h).strip()
                # Check for suspicious data values in headers (e.g. numeric loan IDs)
                if cls.NUMERIC_DATA_PATTERN.match(h_str) or (h_str.isdigit() and len(h_str) > 3):
                    warnings.append(f"Suspicious header detected: '{h_str}' looks like a data value or ID.")

        elif block.block_type == LogicalBlockType.KEY_VALUE_TABLE:
            if not block.items:
                warnings.append(f"Key-value block {block.id} has no extracted pairs.")

        return warnings
