"""
Unit tests for DoclingParser span-aware table grid extraction.

Validates that:
 - row-spanning header cells do NOT bleed their text into sub-header/data rows
 - col-spanning cells align subsequent same-row cells to the correct column
 - Simple tables without spans produce correct rows_raw
"""
from unittest.mock import MagicMock
import pytest

from idp.services.docling.parser import DoclingParser


def _make_tc(r0, c0, r1, c1, text, column_header=False):
    tc = MagicMock()
    tc.start_row_offset_idx = r0
    tc.start_col_offset_idx = c0
    tc.end_row_offset_idx = r1
    tc.end_col_offset_idx = c1
    tc.text = text
    tc.column_header = column_header
    tc.prov = []
    return tc


def _make_table(cells, num_rows, num_cols):
    table = MagicMock()
    table.data.table_cells = cells
    table.data.num_rows = num_rows
    table.data.num_cols = num_cols
    prov = MagicMock()
    prov.page_no = 1
    prov.bbox = None
    table.prov = [prov]
    return table


def _parse_tables(tables_mock):
    parser = DoclingParser()

    class _MockDoc:
        pages = {}
        texts = []
        tables = tables_mock

    mock_conv_result = MagicMock()
    mock_conv_result.document = _MockDoc()
    mock_converter = MagicMock()
    mock_converter.convert.return_value = mock_conv_result

    parser.pipeline.get_converter = lambda: mock_converter
    result = parser.parse("dummy.pdf", doc_id="TEST-GRID")
    return result.tables


def test_simple_table_no_spans():
    cells = [
        _make_tc(0, 0, 0, 0, "H1", column_header=True),
        _make_tc(0, 1, 0, 1, "H2", column_header=True),
        _make_tc(1, 0, 1, 0, "A"),
        _make_tc(1, 1, 1, 1, "B"),
        _make_tc(2, 0, 2, 0, "C"),
        _make_tc(2, 1, 2, 1, "D"),
    ]
    tables = _parse_tables([_make_table(cells, num_rows=3, num_cols=2)])
    assert len(tables) == 1
    t = tables[0]
    assert t.rows_raw[0] == ["H1", "H2"]
    assert t.rows_raw[1] == ["A", "B"]
    assert t.rows_raw[2] == ["C", "D"]


def test_row_spanning_header_no_bleed():
    cells = [
        _make_tc(0, 0, 1, 0, "Section Header", column_header=True),
        _make_tc(0, 1, 0, 1, "Col A", column_header=True),
        _make_tc(1, 1, 1, 1, "Data"),
    ]
    tables = _parse_tables([_make_table(cells, num_rows=2, num_cols=2)])
    t = tables[0]
    assert t.rows_raw[0][0] == "Section Header"
    assert t.rows_raw[0][1] == "Col A"
    assert t.rows_raw[1][0] == "", f"Row-span bleed: row1 col0 = {t.rows_raw[1][0]!r}"
    assert t.rows_raw[1][1] == "Data"


def test_col_spanning_header_alignment():
    cells = [
        _make_tc(0, 0, 0, 1, "Payable to RE", column_header=True),
        _make_tc(0, 2, 0, 2, "Third Party", column_header=True),
        _make_tc(1, 0, 1, 0, "Amount"),
        _make_tc(1, 1, 1, 1, "Type"),
        _make_tc(1, 2, 1, 2, "Amount"),
    ]
    tables = _parse_tables([_make_table(cells, num_rows=2, num_cols=3)])
    t = tables[0]
    assert t.rows_raw[0][0] == "Payable to RE"
    assert t.rows_raw[0][1] == ""
    assert t.rows_raw[0][2] == "Third Party"
    assert t.rows_raw[1] == ["Amount", "Type", "Amount"]


def test_kfs_mixed_span_pattern():
    cells = [
        _make_tc(0, 0, 1, 0, "Installment details", column_header=True),
        _make_tc(0, 1, 0, 1, "Type", column_header=True),
        _make_tc(0, 2, 0, 2, "No.", column_header=True),
        _make_tc(0, 3, 0, 3, "EPI", column_header=True),
        _make_tc(1, 1, 1, 1, "Monthly"),
        _make_tc(1, 2, 1, 2, "36"),
        _make_tc(1, 3, 1, 3, "3586"),
    ]
    tables = _parse_tables([_make_table(cells, num_rows=2, num_cols=4)])
    t = tables[0]
    assert t.rows_raw[0][0] == "Installment details"
    assert t.rows_raw[0][1:] == ["Type", "No.", "EPI"]
    assert t.rows_raw[1][0] == "", f"Row-span bleed: row1 col0 = {t.rows_raw[1][0]!r}"
    assert t.rows_raw[1][1:] == ["Monthly", "36", "3586"]
