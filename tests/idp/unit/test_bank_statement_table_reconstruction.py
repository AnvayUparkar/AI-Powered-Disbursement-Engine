"""
Unit tests verifying PhonePe / UPI bank statement table extraction and reconciliation:
1. Prevents swapped/inverted columns by sorting cells horizontally by spatial coordinate.
2. Prevents transaction rows from being misclassified as headers by HeaderDetector.
3. Prevents column truncation in LogicalTableSegmenter when rows have varying column counts.
4. Reconciles multi-line transaction continuation rows into the parent transaction.
"""
import pytest
from typing import List

from idp.models.table import TableStructure, TableCell
from idp.models.ocr import OCRElement
from idp.services.fusion.spatial_fusion import SpatialCellFusion
from idp.services.table_processing.header_detector import HeaderDetector
from idp.services.table_processing.logical_segmenter import LogicalTableSegmenter
from idp.services.table_processing.reconstructor import LogicalTableReconstructor
from idp.services.table_processing.models import RawRow, RawCell, PhysicalTable, LogicalBlockType


def test_header_detector_rejects_bank_statement_transactions():
    """Validates that transaction rows with dates/IDs/narratives are never classified as headers."""
    txn_row_1 = RawRow(cells=[
        RawCell(text="Aug 27, 2026", column_index=0),
        RawCell(text="?", column_index=1),
        RawCell(text="Paid to SANDIP NAMDEV CHAUGULE Transaction ID T2608271921301203716584 UTR No. 440705273076", column_index=2),
    ])
    assert not HeaderDetector.is_header(txn_row_1)

    txn_row_2 = RawRow(cells=[
        RawCell(text="Sep 02, 2026 09:33 AM", column_index=0),
        RawCell(text="DEBIT", column_index=1),
        RawCell(text="147", column_index=2),
        RawCell(text="Paid to SANGAMLAL C PRASAD Transaction ID T2609020933349254106161 UTR No. 252549013086 Paid by XXXXXX0898", column_index=3),
    ])
    assert not HeaderDetector.is_header(txn_row_2)

    txn_row_3 = RawRow(cells=[
        RawCell(text="Aug 22, 2026 12:11 PM", column_index=0),
        RawCell(text="DEBIT", column_index=1),
        RawCell(text="UTR No. 100221891886 Paid by XXXXXX0898", column_index=2),
    ])
    assert not HeaderDetector.is_header(txn_row_3)


def test_spatial_cell_fusion_preserves_left_to_right_column_order():
    """
    Validates that when TableFormer / Docling outputs cells with out-of-order col_index,
    SpatialCellFusion sorts cells horizontally by spatial X position (c.bbox[0]).
    """
    # Cells given out of order: Details (x=0.7) first, then Date (x=0.05), Type (x=0.35), Amount (x=0.55)
    cells = [
        TableCell(row_index=0, col_index=3, text="", bbox=[700.0, 100.0, 950.0, 140.0]),
        TableCell(row_index=0, col_index=0, text="", bbox=[50.0, 100.0, 300.0, 140.0]),
        TableCell(row_index=0, col_index=1, text="", bbox=[320.0, 100.0, 480.0, 140.0]),
        TableCell(row_index=0, col_index=2, text="", bbox=[500.0, 100.0, 680.0, 140.0]),
    ]
    table = TableStructure(id="tbl-swap-test", page_number=1, num_rows=1, num_cols=4, cells=cells)

    ocr_elements = [
        OCRElement(id="e-date", text="Aug 29, 2026 06:10 PM", bbox=[60.0, 110.0, 280.0, 135.0], confidence=0.99, page_number=1, line_number=1, source="ocr"),
        OCRElement(id="e-type", text="DEBIT", bbox=[330.0, 110.0, 420.0, 135.0], confidence=0.99, page_number=1, line_number=2, source="ocr"),
        OCRElement(id="e-amt", text="167", bbox=[520.0, 110.0, 600.0, 135.0], confidence=0.99, page_number=1, line_number=3, source="ocr"),
        OCRElement(id="e-desc", text="Paid to Raj Ananda UTR No. 395996989303", bbox=[710.0, 110.0, 940.0, 135.0], confidence=0.98, page_number=1, line_number=4, source="ocr"),
    ]

    SpatialCellFusion.fuse_table_cells(
        table=table,
        ocr_elements=ocr_elements,
        page_width=1000.0,
        page_height=1000.0,
        docling_page_width=1000.0,
        docling_page_height=1000.0
    )

    # Validate that columns are strictly left-to-right (Date -> Type -> Amount -> Details)
    assert table.rows_raw == [
        ["Aug 29, 2026 06:10 PM", "DEBIT", "167", "Paid to Raj Ananda UTR No. 395996989303"]
    ]


def test_logical_table_segmenter_never_truncates_columns():
    """
    Validates that when the initial row has 3 columns and subsequent rows have 4 columns,
    LogicalTableSegmenter expands headers and preserves all columns.
    """
    rows_raw = [
        ["Aug 27, 2026", "DEBIT", "Paid to SANDIP Transaction ID T260827 UTR No. 440705"],
        ["Sep 02, 2026 09:33 AM", "DEBIT", "147", "Paid to SANGAMLAL Transaction ID T260902 Paid by XXXXXX0898"],
    ]
    tbl = TableStructure(id="t-no-trunc", page_number=1, num_rows=2, num_cols=4, rows_raw=rows_raw)
    blocks = LogicalTableReconstructor.reconstruct_table(tbl)

    assert len(blocks) == 1
    blk = blocks[0]
    assert blk.block_type == LogicalBlockType.STRUCTURED_TABLE
    # Headers must have expanded to 4 columns
    assert len(blk.headers) == 4
    # All rows must have 4 columns (no truncated columns)
    assert len(blk.rows[0]) == 4
    assert len(blk.rows[1]) == 4
    assert "Paid by XXXXXX0898" in blk.rows[1][3]


def test_phonepe_continuation_rows_reconciled():
    """
    Validates that PhonePe multi-line transaction fragments (e.g. orphan PM, time, UTR, Paid by)
    are merged into the parent row rather than emitted as broken dangling rows.
    """
    rows_raw = [
        ["Aug 28, 2026 06:52 PM", "DEBIT", "212", "Paid to RANJIT RAJENDRA SAH Transaction ID T260828 UTR No. 186070"],
        ["PM", "", ""],
        ["DEBIT", "Paid by XXXXXX0898", ""],
    ]
    tbl = TableStructure(id="t-cont-reconcile", page_number=1, num_rows=3, num_cols=4, rows_raw=rows_raw)
    blocks = LogicalTableReconstructor.reconstruct_table(tbl)

    assert len(blocks) == 1
    blk = blocks[0]
    # The 2 continuation rows should be absorbed into the main transaction
    assert len(blk.rows) == 1
    merged_txn = blk.rows[0]
    assert "Paid by XXXXXX0898" in merged_txn[1] or "Paid by XXXXXX0898" in merged_txn[3]
