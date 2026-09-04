import pytest
from idp.models.table import TableStructure, TableCell
from idp.services.table_processing.models import (
    RawCell, RawRow, PhysicalTable, RowType, LogicalBlockType, LogicalBlock
)
from idp.services.table_processing.kv_detector import KeyValueDetector
from idp.services.table_processing.header_detector import HeaderDetector
from idp.services.table_processing.logical_segmenter import LogicalTableSegmenter
from idp.services.table_processing.continuation_detector import ContinuationDetector
from idp.services.table_processing.reconstructor import LogicalTableReconstructor
from idp.services.table_processing.validator import TableValidator


def test_scenario_1_single_key_value_row():
    """Test 1 — Key-value row: 1 | Loan proposal/account No. | 79481049."""
    cells = [
        RawCell(text="1", column_index=0),
        RawCell(text="Loan proposal/account No.", column_index=1),
        RawCell(text="79481049", column_index=2),
    ]
    pairs = KeyValueDetector.extract_pairs(cells)
    assert len(pairs) == 1
    assert pairs[0]["field"] == "Loan proposal/account No."
    assert pairs[0]["value"] == "79481049"


def test_scenario_2_two_key_value_pairs():
    """Test 2 — Two key-value pairs: 1 | Loan Account Number | 79481049 | Type of Loan | RELPL."""
    cells = [
        RawCell(text="1", column_index=0),
        RawCell(text="Loan Account Number", column_index=1),
        RawCell(text="79481049", column_index=2),
        RawCell(text="Type of Loan", column_index=3),
        RawCell(text="RELPL", column_index=4),
    ]
    pairs = KeyValueDetector.extract_pairs(cells)
    assert len(pairs) == 2
    assert pairs[0] == {"field": "Loan Account Number", "value": "79481049"}
    assert pairs[1] == {"field": "Type of Loan", "value": "RELPL"}


def test_scenario_3_installment_nested_table():
    """Test 3 — Installment nested table with 4 columns."""
    row_hdr = RawRow(
        cells=[
            RawCell(text="Type of installments", column_index=0),
            RawCell(text="Number of EPIs", column_index=1),
            RawCell(text="EPI (₹)", column_index=2),
            RawCell(text="Commencement of repayment, post sanction", column_index=3),
        ],
        row_index=0
    )
    row_data = RawRow(
        cells=[
            RawCell(text="Monthly", column_index=0),
            RawCell(text="12", column_index=1),
            RawCell(text="2799", column_index=2),
            RawCell(text="04/04/2026", column_index=3),
        ],
        row_index=1
    )

    phys_table = PhysicalTable(
        id="tbl-installment",
        page_number=1,
        rows=[row_hdr, row_data]
    )

    blocks = LogicalTableSegmenter.segment(phys_table)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.block_type == LogicalBlockType.STRUCTURED_TABLE
    assert len(b.headers) == 4
    assert b.headers == [
        "Type of installments",
        "Number of EPIs",
        "EPI (₹)",
        "Commencement of repayment, post sanction"
    ]
    assert len(b.rows) == 1
    assert b.rows[0] == ["Monthly", "12", "2799", "04/04/2026"]
    assert b.structured_rows[0]["Number of EPIs"] == "12"
    assert "Monthly" in b.structured_rows[0].values()


def test_scenario_4_mixed_physical_table():
    """
    Test 4 — Mixed physical table containing:
    - Loan Details (key-value)
    - Installment Details (structured table)
    - Interest Details (key-value)
    """
    r0 = RawRow(cells=[RawCell(text="Loan Details")], row_index=0)
    r1 = RawRow(cells=[RawCell(text="1"), RawCell(text="Loan proposal/account No."), RawCell(text="79481049")], row_index=1)
    r2 = RawRow(cells=[RawCell(text="Sanctioned Loan amount"), RawCell(text="28000.00")], row_index=2)

    r3 = RawRow(cells=[RawCell(text="Installment Details")], row_index=3)
    r4 = RawRow(
        cells=[
            RawCell(text="Type of installments"),
            RawCell(text="Number of EPIs"),
            RawCell(text="EPI (₹)"),
            RawCell(text="Commencement of repayment")
        ],
        row_index=4
    )
    r5 = RawRow(cells=[RawCell(text="Monthly"), RawCell(text="12"), RawCell(text="2799"), RawCell(text="04/04/2026")], row_index=5)

    r6 = RawRow(cells=[RawCell(text="Interest Details")], row_index=6)
    r7 = RawRow(cells=[RawCell(text="Interest rate type"), RawCell(text="Fixed")], row_index=7)
    r8 = RawRow(cells=[RawCell(text="Rate of Interest"), RawCell(text="35.00 %")], row_index=8)

    phys_table = PhysicalTable(
        id="tbl-mixed",
        page_number=1,
        rows=[r0, r1, r2, r3, r4, r5, r6, r7, r8]
    )

    blocks = LogicalTableSegmenter.segment(phys_table)
    # Expected: 3 separate logical blocks
    assert len(blocks) == 3

    # Block 1: Loan Details (KEY_VALUE_TABLE)
    assert blocks[0].block_type == LogicalBlockType.KEY_VALUE_TABLE
    assert len(blocks[0].items) == 2
    assert blocks[0].items[0]["field"] == "Loan proposal/account No."

    # Block 2: Installment Details (STRUCTURED_TABLE)
    assert blocks[1].block_type == LogicalBlockType.STRUCTURED_TABLE
    assert len(blocks[1].headers) == 4
    assert len(blocks[1].rows) == 1
    assert blocks[1].rows[0][0] == "Monthly"

    # Block 3: Interest Details (KEY_VALUE_TABLE)
    assert blocks[2].block_type == LogicalBlockType.KEY_VALUE_TABLE
    assert len(blocks[2].items) == 2
    assert blocks[2].items[0]["field"] == "Interest rate type"
    assert blocks[2].items[0]["value"] == "Fixed"


def test_scenario_5_repayment_schedule():
    """Test 5 — Repayment schedule: 9 columns remain fully preserved."""
    headers = [
        "Instl. Num.", "Due Date", "Opening Principal", "Inst. Amt.",
        "Principal", "Interest", "Adv Flag", "Closing Principal", "Rate(%)"
    ]
    r_hdr = RawRow(cells=[RawCell(text=h, column_index=i) for i, h in enumerate(headers)], row_index=0)
    data1 = ["1", "04/04/2026", "28,000.00", "2,799.00", "1,982.00", "817.00", "No", "26,018.00", "35.00"]
    data2 = ["2", "04/05/2026", "26,018.00", "2,799.00", "2,040.00", "759.00", "No", "23,978.00", "35.00"]
    r_data1 = RawRow(cells=[RawCell(text=d, column_index=i) for i, d in enumerate(data1)], row_index=1)
    r_data2 = RawRow(cells=[RawCell(text=d, column_index=i) for i, d in enumerate(data2)], row_index=2)

    phys_table = PhysicalTable(id="tbl-repay", page_number=4, rows=[r_hdr, r_data1, r_data2])
    blocks = LogicalTableSegmenter.segment(phys_table)

    assert len(blocks) == 1
    b = blocks[0]
    assert b.block_type == LogicalBlockType.STRUCTURED_TABLE
    assert b.headers == headers
    assert len(b.rows) == 2
    assert b.rows[0] == data1
    assert b.rows[1] == data2
    assert b.structured_rows[0]["Opening Principal"] == "28,000.00"


def test_scenario_6_total_row_separation():
    """Test 6 — Total row: TOTAL is stored as summary row and does not corrupt column mapping."""
    headers = ["Instl. Num.", "Due Date", "Opening Principal", "Inst. Amt."]
    r_hdr = RawRow(cells=[RawCell(text=h, column_index=i) for i, h in enumerate(headers)], row_index=0)
    r_data = RawRow(cells=[RawCell(text=v, column_index=i) for i, v in enumerate(["1", "04/04/2026", "28,000.00", "2,799.00"])], row_index=1)
    r_total = RawRow(cells=[RawCell(text=v, column_index=i) for i, v in enumerate(["TOTAL", "33,586.00", "28,000.00", "5,586.00"])], row_index=2)

    phys_table = PhysicalTable(id="tbl-total", page_number=4, rows=[r_hdr, r_data, r_total])
    blocks = LogicalTableSegmenter.segment(phys_table)

    assert len(blocks) == 1
    b = blocks[0]
    assert len(b.rows) == 1  # only data row in normal rows
    assert b.rows[0] == ["1", "04/04/2026", "28,000.00", "2,799.00"]
    assert len(b.summary_rows) == 1
    assert b.summary_rows[0]["label"] == "TOTAL"
    assert b.summary_rows[0]["values"] == ["33,586.00", "28,000.00", "5,586.00"]


def test_scenario_7_cross_page_continuation():
    """
    Test 7 — Cross-page continuation:
    Page 1: items 1, 2
    Page 2: items 6, 7
    """
    b1 = LogicalBlock(
        id="contingent-charges-1",
        page_number=1,
        page_numbers=[1],
        block_type=LogicalBlockType.KEY_VALUE_TABLE,
        title="Contingent Charges",
        items=[
            {"field": "1 Documentation Charges", "value": "Upto 3.54%"},
            {"field": "2 Convenience charges", "value": "Rs. 1500"},
        ]
    )
    b2 = LogicalBlock(
        id="contingent-charges-2",
        page_number=2,
        page_numbers=[2],
        block_type=LogicalBlockType.KEY_VALUE_TABLE,
        title="Contingent Charges",
        items=[
            {"field": "6 Reschedulement Charges", "value": "1.18%"},
            {"field": "7 Loan Recall Notice Charges", "value": "Rs. 2360"},
        ]
    )

    merged = ContinuationDetector.merge_continuations([b1, b2])
    assert len(merged) == 1
    unified = merged[0]
    assert unified.page_numbers == [1, 2]
    assert len(unified.items) == 4
    assert unified.items[0]["field"] == "1 Documentation Charges"
    assert unified.items[3]["field"] == "7 Loan Recall Notice Charges"


def test_validator_detects_suspicious_header():
    """TableValidator warns on data values mistakenly passed as headers."""
    b = LogicalBlock(
        id="bad-table",
        page_number=1,
        block_type=LogicalBlockType.STRUCTURED_TABLE,
        headers=["1", "Loan proposal", "79481049", "RELPL"],
        rows=[["a", "b", "c", "d"]]
    )
    warnings = TableValidator.validate(b)
    assert any("Suspicious header detected" in w for w in warnings)
