from typing import List, Optional
from idp.models.table import TableStructure
from idp.services.table_processing.models import (
    PhysicalTable, RawRow, RawCell, LogicalBlock, LogicalBlockType
)
from idp.services.table_processing.logical_segmenter import LogicalTableSegmenter
from idp.services.table_processing.continuation_detector import ContinuationDetector
from idp.services.table_processing.validator import TableValidator


class LogicalTableReconstructor:
    """
    Transforms raw physical table structures into normalized logical blocks
    and clean markdown table strings for downstream LLM adjudication.
    """

    @classmethod
    def physical_from_table_structure(cls, table: TableStructure) -> PhysicalTable:
        """Converts a canonical TableStructure into PhysicalTable for segmentation."""
        raw_rows: List[RawRow] = []

        if table.rows_raw:
            for r_idx, r_vals in enumerate(table.rows_raw):
                cells = [
                    RawCell(text=str(val or "").strip(), column_index=c_idx)
                    for c_idx, val in enumerate(r_vals)
                ]
                raw_rows.append(RawRow(cells=cells, row_index=r_idx))
        elif table.cells:
            row_map = {}
            for cell in table.cells:
                row_map.setdefault(cell.row_index, []).append(cell)
            for r_idx in sorted(row_map.keys()):
                sorted_cells = sorted(row_map[r_idx], key=lambda c: c.col_index)
                raw_cells = [
                    RawCell(text=str(c.text or "").strip(), column_index=c.col_index, bbox=c.bbox)
                    for c in sorted_cells
                ]
                raw_rows.append(RawRow(cells=raw_cells, row_index=r_idx))

        return PhysicalTable(
            id=table.id or "tbl-1",
            page_number=table.page_number,
            rows=raw_rows,
            bbox=table.bbox
        )

    @classmethod
    def reconstruct_table(cls, table: TableStructure) -> List[LogicalBlock]:
        """Segments and validates a canonical TableStructure into logical blocks."""
        phys = cls.physical_from_table_structure(table)
        blocks = LogicalTableSegmenter.segment(phys)
        for b in blocks:
            warnings = TableValidator.validate(b)
        return blocks

    @classmethod
    def block_to_markdown(cls, block: LogicalBlock) -> str:
        """Converts a LogicalBlock into standardized [TABLE] markdown representation."""
        lines = []
        if block.title:
            lines.append(f"### {block.title}")

        lines.append("[TABLE]")
        if block.block_type == LogicalBlockType.STRUCTURED_TABLE:
            if block.headers:
                lines.append(" | ".join(block.headers))
            for row in block.rows:
                lines.append(" | ".join(row))
            for s_row in block.summary_rows:
                lbl = s_row.get("label", "TOTAL")
                vals = s_row.get("values", [])
                lines.append(f"{lbl} | " + " | ".join(vals))
        elif block.block_type == LogicalBlockType.KEY_VALUE_TABLE:
            lines.append("Field | Value")
            for item in block.items:
                f_name = item.get("field", "")
                v_val = item.get("value", "")
                lines.append(f"{f_name} | {v_val}")
        lines.append("[/TABLE]")

        return "\n".join(lines)
