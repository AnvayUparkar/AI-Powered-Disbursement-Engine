"""
Tests for digital PDF comb-box element replacement and narrow glyph merging.
"""

import pytest
from idp.services.extraction.comb_box_detector import CombBoxDetector
from idp.models.layout import LayoutElement, ElementType


def _make_char_element(id_str: str, char: str, x0: float, y0: float, box_w: float = 18.0, box_h: float = 20.0):
    return LayoutElement(
        id=id_str,
        type=ElementType.TEXT,
        text=char,
        bbox=[x0, y0, x0 + box_w, y0 + box_h],
        confidence=0.95,
        page_number=1,
        reading_order=int(id_str.split("-")[-1]) if id_str.rsplit("-", 1)[-1].isdigit() else 0,
        source="docling_ocr",
        structure_source="docling",
    )


class TestDigitalCombBoxFixes:
    def test_narrow_digit_one_and_letter_i_merging(self):
        """Digital PDF single-character glyphs with narrow aspect ratios ('1', 'I') must merge successfully."""
        detector = CombBoxDetector()
        chars = ["1", "3", "0", "4", "1", "9", "9", "0"]
        elements = []
        x = 100.0
        for i, ch in enumerate(chars):
            # Thin glyph for '1' (width 4.0), wider glyph for '0' (width 12.0)
            w = 4.0 if ch == "1" else 12.0
            elements.append(_make_char_element(f"elem-{i}", ch, x, 200.0, box_w=w, box_h=20.0))
            x += w + 2.0  # 2.0 gap

        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 1
        assert merged[0].text == "13041990"

    def test_multi_word_name_sub_run_splitting(self):
        """Names with spaces (e.g., AKSHALI MUTTURAJA) must split into 2 clean merged tokens."""
        detector = CombBoxDetector()
        elements = []
        x = 50.0

        # First name: AKSHALI
        for i, ch in enumerate("AKSHALI"):
            elements.append(_make_char_element(f"fn-{i}", ch, x, 150.0, box_w=12.0, box_h=20.0))
            x += 14.0

        # Empty grid boxes gap (30px)
        x += 30.0

        # Surname: MUTTURAJA
        for i, ch in enumerate("MUTTURAJA"):
            elements.append(_make_char_element(f"ln-{i}", ch, x, 150.0, box_w=12.0, box_h=20.0))
            x += 14.0

        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 2
        assert merged[0].text == "AKSHALI"
        assert merged[1].text == "MUTTURAJA"

    def test_mixed_multichar_token_merging(self):
        """Mixed multi-character tokens ('1', '30', '41', '990') produced by digital OCR must merge into '13041990'."""
        detector = CombBoxDetector()
        tokens = [("1", 1), ("30", 2), ("41", 2), ("990", 3)]
        elements = []
        x = 100.0
        cell_pitch = 18.0
        for i, (tok, n_chars) in enumerate(tokens):
            w = n_chars * cell_pitch - 2.0
            elements.append(_make_char_element(f"mc-{i}", tok, x, 250.0, box_w=w, box_h=20.0))
            x += w + 2.0

        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 1
        assert merged[0].text == "13041990"

    def test_karnataka_with_missing_character_and_multichar_chunk(self):
        """K, AR, (missing N), ATAK, A should merge into KARATAKA despite missing N and 4-char ATAK chunk."""
        detector = CombBoxDetector()
        elements = []
        x = 100.0
        pitch = 18.0

        # K (1 char)
        elements.append(_make_char_element("k-1", "K", x, 150.0, box_w=16.0, box_h=20.0))
        x += pitch

        # AR (2 chars)
        elements.append(_make_char_element("k-2", "AR", x, 150.0, box_w=34.0, box_h=20.0))
        x += 2 * pitch

        # Missing N (advance by 1 pitch without adding element)
        x += pitch

        # ATAK (4 chars)
        elements.append(_make_char_element("k-3", "ATAK", x, 150.0, box_w=70.0, box_h=20.0))
        x += 4 * pitch

        # A (1 char)
        elements.append(_make_char_element("k-4", "A", x, 150.0, box_w=16.0, box_h=20.0))

        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 1
        assert merged[0].text == "KARATAKA"

    def test_digital_narrow_ones_with_wide_pitch(self):
        """Digital '1' glyphs with small width (3.5pt) and wide cell pitch (26pt) must merge without advance-jump rejection."""
        detector = CombBoxDetector()
        elements = []
        x = 100.0
        pitch = 26.0
        for i in range(4):
            # Width 3.5pt, height 20pt -> old med_w * 5.0 was 17.5pt, rejecting 26pt pitch
            elements.append(_make_char_element(f"one-{i}", "1", x, 150.0, box_w=3.5, box_h=20.0))
            x += pitch

        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 1
        assert merged[0].text == "1111"

    def test_partially_filled_comb_table_reclassification(self):
        """A 16-cell grid with 8 filled digits and 8 empty cells must be reclassified as RECLASSIFY_AS_COMB_BOX."""
        from idp.models.table import TableStructure, TableCell
        from idp.services.output.serializer import DocumentSerializer, TableShapeDecision

        cells = []
        digits = ["2", "6", "3", "8", "8", "7", "1", "4"]
        for col in range(16):
            txt = digits[col] if col < len(digits) else ""
            cells.append(TableCell(
                row_index=0,
                col_index=col,
                text=txt,
                bbox=[100.0 + col * 20.0, 200.0, 118.0 + col * 20.0, 220.0],
                confidence=0.9
            ))

        table = TableStructure(
            id="partially-filled-comb-tbl",
            page_number=1,
            cells=cells,
            bbox=[100.0, 200.0, 420.0, 220.0]
        )

        decision = DocumentSerializer._classify_table_shape(
            table=table,
            reclassify_ratio=0.8,
            ambiguous_floor_ratio=0.5
        )
        assert decision == TableShapeDecision.RECLASSIFY_AS_COMB_BOX

    def test_all_caps_fused_comb_row_detection(self):
        """All-caps lines without colons must be recognized as fused comb rows in CombGridDetector."""
        from idp.services.extraction.comb_grid_detector import CombGridDetector

        # Numeric application number in all-caps row
        assert CombGridDetector.is_fused_comb_row(
            "APPLICATION NO 2638871426000308",
            [50.0, 100.0, 450.0, 120.0]
        ) is True

        # Name field in all-caps row
        assert CombGridDetector.is_fused_comb_row(
            "APPLICANT NAME PRAKASH KHATRI",
            [50.0, 100.0, 450.0, 120.0]
        ) is True
