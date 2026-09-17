"""
Unit tests for comb-box anti-fragmentation, multi-row separation,
multi-word empty cell joining, and special character retention.
"""

import pytest
from idp.services.extraction.comb_box_detector import CombBoxDetector
from idp.models.layout import LayoutElement, ElementType


def _make_elem(id_str: str, text: str, x0: float, y0: float, w: float = 12.0, h: float = 14.0) -> LayoutElement:
    return LayoutElement(
        id=id_str,
        type=ElementType.TEXT,
        text=text,
        bbox=[x0, y0, x0 + w, y0 + h],
        confidence=0.92,
        page_number=1,
        source="docling_ocr",
        structure_source="docling",
    )


class TestCombBoxAntiFragmentation:
    def test_multi_row_separation_prevents_vertical_collapsing(self):
        """Application Date (y=103) and LOS No (y=116) must cluster into 2 separate rows and not interleave."""
        detector = CombBoxDetector()
        elements = []

        # Row 1: Application Date digits 03072026 (y=103..113, h=10)
        date_chars = ["0", "3", "0", "7", "2", "0", "2", "6"]
        x = 100.0
        pitch = 14.0
        for i, ch in enumerate(date_chars):
            elements.append(_make_elem(f"date-{i}", ch, x, 103.0, w=10.0, h=10.0))
            x += pitch

        # Row 2: LOS No APPL00243685 (y=116..127, h=11)
        los_chars = ["A", "P", "P", "L", "0", "0", "2", "4", "3", "6", "8", "5"]
        x = 100.0
        for i, ch in enumerate(los_chars):
            elements.append(_make_elem(f"los-{i}", ch, x, 116.0, w=11.0, h=11.0))
            x += pitch

        # Interleave elements in the candidate list to test clustering resilience
        interleaved = []
        for i in range(max(len(date_chars), len(los_chars))):
            if i < len(date_chars):
                interleaved.append(elements[i])
            if i < len(los_chars):
                interleaved.append(elements[len(date_chars) + i])

        rows = detector._cluster_by_row(interleaved)
        assert len(rows) == 2, f"Expected exactly 2 distinct rows, got {len(rows)}"

        # Verify that each row contains solely its own line
        row_0_texts = "".join(e.text for e in sorted(rows[0], key=lambda e: e.bbox[0]))
        row_1_texts = "".join(e.text for e in sorted(rows[1], key=lambda e: e.bbox[0]))
        assert row_0_texts == "03072026"
        assert row_1_texts == "APPL00243685"

        merged = detector.detect_and_merge_comb_boxes(interleaved, page_number=1, doc_id="TEST")
        assert len(merged) == 2
        merged_texts = {m.text for m in merged}
        assert "03072026" in merged_texts
        assert "APPL00243685" in merged_texts

    def test_multi_word_name_with_empty_comb_spacer_cell(self):
        """Applicant Name (AKSHALI [empty cell] MUTTURAJA) must merge into a single token with space."""
        detector = CombBoxDetector()
        elements = []
        x = 50.0
        pitch = 14.0

        # First name: AKSHALI (7 chars)
        for i, ch in enumerate("AKSHALI"):
            elements.append(_make_elem(f"fn-{i}", ch, x, 150.0, w=11.0, h=14.0))
            x += pitch

        # 1 Empty comb box cell (advance by 1 cell pitch)
        x += pitch

        # Surname: MUTTURAJA (9 chars)
        for i, ch in enumerate("MUTTURAJA"):
            elements.append(_make_elem(f"ln-{i}", ch, x, 150.0, w=11.0, h=14.0))
            x += pitch

        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 1, f"Expected 1 merged token for entire name, got {len(merged)}"
        assert merged[0].text == "AKSHALI MUTTURAJA"
        assert merged[0].metadata["num_constituents"] == 16

    def test_email_candidate_retention_and_merging(self):
        """Email address with '@' and '.' must pass is_candidate_text and merge into complete email."""
        detector = CombBoxDetector()
        email_str = "MUTTUR508@GMAIL.COM"

        # Verify is_candidate_text accepts single symbols
        assert detector.is_candidate_text("@") is True
        assert detector.is_candidate_text(".") is True
        assert detector.is_candidate_text("-") is True
        assert detector.is_candidate_text("/") is True

        elements = []
        x = 60.0
        pitch = 13.0
        for i, ch in enumerate(email_str):
            elements.append(_make_elem(f"em-{i}", ch, x, 220.0, w=10.0, h=14.0))
            x += pitch

        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 1
        assert merged[0].text == "MUTTUR508@GMAIL.COM"

    def test_long_single_word_mother_name_merging(self):
        """13-character single word VANAJAKSHAMMA must merge into a single token."""
        detector = CombBoxDetector()
        name_str = "VANAJAKSHAMMA"
        elements = []
        x = 40.0
        pitch = 14.0
        for i, ch in enumerate(name_str):
            elements.append(_make_elem(f"m-{i}", ch, x, 180.0, w=11.0, h=14.0))
            x += pitch

        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 1
        assert merged[0].text == "VANAJAKSHAMMA"
        assert merged[0].metadata["num_constituents"] == 13

    def test_empty_and_single_element_boundary_conditions(self):
        """Empty element list and isolated single elements return empty merged tokens without error."""
        detector = CombBoxDetector()
        assert detector.detect_and_merge_comb_boxes([], page_number=1) == []

        single = [_make_elem("s-1", "A", 10.0, 10.0)]
        assert detector.detect_and_merge_comb_boxes(single, page_number=1) == []
