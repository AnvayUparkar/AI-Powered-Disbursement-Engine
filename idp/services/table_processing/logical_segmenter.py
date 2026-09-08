from typing import List, Dict, Any, Optional
from idp.services.table_processing.models import (
    PhysicalTable, RawRow, RawCell, LogicalBlock, LogicalBlockType
)
from idp.services.table_processing.kv_detector import KeyValueDetector
from idp.services.table_processing.header_detector import HeaderDetector


class LogicalTableSegmenter:
    """Segments complex physical tables into structured tables and key-value blocks."""

    @classmethod
    def is_section_header_row(cls, row: RawRow) -> bool:
        """Checks if a row is a section title (e.g. 'Loan Details', 'Installment Details')."""
        non_empty = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
        if len(non_empty) == 1:
            txt = non_empty[0]
            # If not purely numeric or index
            if len(txt) > 2 and not txt.isdigit():
                return True
        return False

    @classmethod
    def is_summary_row(cls, row: RawRow) -> Tuple_summary if False else bool: # type: ignore
        non_empty = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
        if non_empty and non_empty[0].upper().startswith("TOTAL"):
            return True
        return False

    @classmethod
    def segment(cls, phys_table: PhysicalTable) -> List[LogicalBlock]:
        """
        Segments a physical table into logical blocks based on row structure,
        headers, and key-value detection.
        """
        if not phys_table.rows:
            return []

        blocks: List[LogicalBlock] = []
        current_title: Optional[str] = None
        current_type: Optional[LogicalBlockType] = None
        current_headers: List[str] = []
        current_rows: List[List[str]] = []
        current_structured_rows: List[Dict[str, str]] = []
        current_summary_rows: List[Dict[str, Any]] = []
        current_items: List[Dict[str, str]] = []

        def flush_block():
            nonlocal current_type, current_headers, current_rows, current_structured_rows, current_summary_rows, current_items, current_title
            if current_type is None:
                return

            block_id = f"{phys_table.id}-block-{len(blocks) + 1}"
            if current_type == LogicalBlockType.KEY_VALUE_TABLE and current_items:
                blocks.append(
                    LogicalBlock(
                        id=block_id,
                        page_number=phys_table.page_number,
                        page_numbers=[phys_table.page_number],
                        block_type=LogicalBlockType.KEY_VALUE_TABLE,
                        title=current_title,
                        items=list(current_items)
                    )
                )
            elif current_type == LogicalBlockType.STRUCTURED_TABLE and (current_headers or current_rows):
                blocks.append(
                    LogicalBlock(
                        id=block_id,
                        page_number=phys_table.page_number,
                        page_numbers=[phys_table.page_number],
                        block_type=LogicalBlockType.STRUCTURED_TABLE,
                        title=current_title,
                        headers=list(current_headers),
                        rows=list(current_rows),
                        structured_rows=list(current_structured_rows),
                        summary_rows=list(current_summary_rows)
                    )
                )

            current_headers = []
            current_rows = []
            current_structured_rows = []
            current_summary_rows = []
            current_items = []
            current_type = None

        for row in phys_table.rows:
            active_texts = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
            if not active_texts:
                continue

            # Case 1: Section Title row (e.g. "Loan Details", "Installment Details")
            if cls.is_section_header_row(row):
                flush_block()
                current_title = active_texts[0]
                continue

            # Case 2: Summary row (e.g. "TOTAL", "33,586.00", ...)
            if active_texts[0].upper().startswith("TOTAL"):
                label = active_texts[0]
                vals = active_texts[1:]
                current_summary_rows.append({"label": label, "values": vals})
                continue

            # Case 3: Header row for a structured table
            if HeaderDetector.is_header(row):
                # If we were in a key-value table, flush it
                if current_type == LogicalBlockType.KEY_VALUE_TABLE:
                    flush_block()
                current_type = LogicalBlockType.STRUCTURED_TABLE
                current_headers = active_texts
                continue

            # Case 4: Inside a structured table
            if current_type == LogicalBlockType.STRUCTURED_TABLE and current_headers:
                # Row belongs to the structured table if cell count aligns or is data
                # Map values to headers
                row_vals = [c.text.strip() for c in row.cells]
                # Pad or trim to header length if needed
                if len(row_vals) < len(current_headers):
                    row_vals.extend([""] * (len(current_headers) - len(row_vals)))
                elif len(row_vals) > len(current_headers):
                    row_vals = row_vals[:len(current_headers)]

                current_rows.append(row_vals)
                struct_map = {
                    h: row_vals[i] if i < len(row_vals) else ""
                    for i, h in enumerate(current_headers)
                }
                current_structured_rows.append(struct_map)
                continue

            # Case 5: Key-Value row
            pairs = KeyValueDetector.extract_pairs(row.cells)
            if pairs:
                if current_type == LogicalBlockType.STRUCTURED_TABLE:
                    flush_block()
                current_type = LogicalBlockType.KEY_VALUE_TABLE
                current_items.extend(pairs)
                continue

            # Fallback for data row when no header was explicitly declared
            if current_type is None and len(active_texts) >= 2:
                # If first row looks like key-value, treat as key-value
                pairs = KeyValueDetector.extract_pairs(row.cells)
                if pairs:
                    current_type = LogicalBlockType.KEY_VALUE_TABLE
                    current_items.extend(pairs)
                else:
                    current_type = LogicalBlockType.STRUCTURED_TABLE
                    current_headers = [f"Col {i+1}" for i in range(len(active_texts))]
                    current_rows.append(active_texts)

        flush_block()
        return blocks
