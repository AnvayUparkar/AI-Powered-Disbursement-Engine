"""
Comprehensive unit tests for comb-box detection system.

Tests spatial clustering, uniformity detection, sequence merging,
validation, and integration scenarios.
"""

import pytest
from idp.models.layout import LayoutElement, ElementType
from idp.models.merged_token import MergedToken
from idp.services.extraction.comb_box_detector import CombBoxDetector
from idp.services.extraction.field_validator import FieldValidator, ValidationResult


class TestCombBoxDetector:
    """Test core comb-box detection engine."""
    
    def test_detect_uniform_spacing(self):
        """Verify uniform gap detection."""
        detector = CombBoxDetector()
        
        elements = [
            LayoutElement(
                id="e1", text="A", bbox=[100, 200, 110, 210],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=1
            ),
            LayoutElement(
                id="e2", text="P", bbox=[115, 200, 125, 210],  # Gap: 5px
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=2
            ),
            LayoutElement(
                id="e3", text="P", bbox=[130, 200, 140, 210],  # Gap: 5px
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=3
            ),
            LayoutElement(
                id="e4", text="L", bbox=[145, 200, 155, 210],  # Gap: 5px
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=4
            ),
        ]
        
        is_uniform, score = detector._is_comb_box_sequence(elements)
        assert is_uniform is True
        assert score > 0.8
    
    def test_reject_non_uniform_spacing(self):
        """Verify irregular spacing rejected."""
        detector = CombBoxDetector()
        
        elements = [
            LayoutElement(
                id="e1", text="A", bbox=[100, 200, 110, 210],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=1
            ),
            LayoutElement(
                id="e2", text="P", bbox=[115, 200, 125, 210],  # Gap: 5px
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=2
            ),
            LayoutElement(
                id="e3", text="P", bbox=[150, 200, 160, 210],  # Gap: 25px (outlier)
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=3
            ),
        ]
        
        is_uniform, score = detector._is_comb_box_sequence(elements)
        assert is_uniform is False
    
    def test_merge_sequence(self):
        """Verify text concatenation and bbox merging."""
        detector = CombBoxDetector()
        
        elements = [
            LayoutElement(
                id="e1", text="A", bbox=[100, 200, 110, 210],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=1
            ),
            LayoutElement(
                id="e2", text="P", bbox=[115, 200, 125, 210],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=2
            ),
            LayoutElement(
                id="e3", text="P", bbox=[130, 200, 140, 210],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=3
            ),
            LayoutElement(
                id="e4", text="L", bbox=[145, 200, 155, 210],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=4
            ),
        ]
        
        result = detector._merge_sequence(elements, page_number=1, doc_id="TEST")
        
        assert result is not None
        assert result.text == "APPL"
        assert result.bbox == [100, 200, 155, 210]
        assert result.page_number == 1
        assert result.source_type == "comb_box_reconstruction"
        assert len(result.constituent_element_ids) == 4
        assert result.confidence > 0.8
    
    def test_application_no_reconstruction(self):
        """End-to-end: Individual chars → merged application number."""
        detector = CombBoxDetector()
        
        # Simulate "APPL00343265" as individual characters
        chars = list("APPL00343265")
        elements = []
        x_pos = 100
        for i, char in enumerate(chars):
            elements.append(
                LayoutElement(
                    id=f"e{i}", text=char, bbox=[x_pos, 200, x_pos + 10, 210],
                    page_number=1, type=ElementType.TEXT, confidence=0.9,
                    source="docling_ocr", structure_source="docling", reading_order=i
                )
            )
            x_pos += 15  # Uniform 5px gap
        
        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        
        assert len(merged) == 1
        assert merged[0].text == "APPL00343265"
        assert merged[0].source_type == "comb_box_reconstruction"
        assert merged[0].uniformity_score > 0.7
    
    def test_cluster_by_row(self):
        """Test horizontal row clustering."""
        detector = CombBoxDetector()
        
        # Create elements in 2 rows
        row1 = [
            LayoutElement(
                id="r1e1", text="A", bbox=[100, 200, 110, 210],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=1
            ),
            LayoutElement(
                id="r1e2", text="B", bbox=[115, 202, 125, 212],  # Slightly offset y
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=2
            ),
        ]
        
        row2 = [
            LayoutElement(
                id="r2e1", text="C", bbox=[100, 250, 110, 260],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=3
            ),
            LayoutElement(
                id="r2e2", text="D", bbox=[115, 251, 125, 261],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=4
            ),
        ]
        
        all_elements = row1 + row2
        rows = detector._cluster_by_row(all_elements)
        
        assert len(rows) == 2
        assert len(rows[0]) == 2
        assert len(rows[1]) == 2
    
    def test_reject_word_sequences(self):
        """Verify multi-char words not treated as comb-box."""
        detector = CombBoxDetector()
        
        # Normal words, not comb-box
        elements = [
            LayoutElement(
                id="e1", text="Hello", bbox=[100, 200, 150, 210],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=1
            ),
            LayoutElement(
                id="e2", text="World", bbox=[160, 200, 210, 210],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=2
            ),
        ]
        
        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        
        # Should not merge (text length > max_char_length)
        assert len(merged) == 0
    
    def test_realistic_handwritten_name_width_variance(self):
        """
        Regression test for the production bug: real OCR'd handwriting has
        genuine per-glyph width variance (a narrow "I" vs a wide "K"/"H")
        even when written inside a perfectly uniform printed comb-box grid.
        The original 0.25 size_uniformity_threshold rejected this and
        silently produced 0 merged tokens / 0 bounding box for real name
        fields. Widths below are representative ink-extent measurements for
        "KHATRI" handwritten in a comb-box row with ~15px cell pitch.
        """
        detector = CombBoxDetector()

        chars = "KHATRI"
        widths = [11, 12, 10, 9, 10, 4]  # "I" is intentionally narrow
        elements = []
        x_pos = 100
        pitch = 15
        for i, (char, w) in enumerate(zip(chars, widths)):
            elements.append(
                LayoutElement(
                    id=f"e{i}", text=char, bbox=[x_pos, 200, x_pos + w, 215],
                    page_number=1, type=ElementType.TEXT, confidence=0.85,
                    source="docling_ocr", structure_source="docling", reading_order=i
                )
            )
            x_pos += pitch

        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")

        assert len(merged) == 1
        assert merged[0].text == "KHATRI"
        assert merged[0].bbox is not None

    def test_min_sequence_length(self):
        """Verify minimum sequence length enforced."""
        detector = CombBoxDetector(min_sequence_length=4)
        
        # Only 3 chars
        elements = [
            LayoutElement(
                id=f"e{i}", text=char, bbox=[100 + i*15, 200, 110 + i*15, 210],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="test", structure_source="test", reading_order=i
            )
            for i, char in enumerate("ABC")
        ]
        
        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")

        # Should not merge (< min_sequence_length)
        assert len(merged) == 0

    def test_reject_vertical_stack(self):
        """A vertical column of single characters (e.g. a numbered list
        "1"/"2"/"3" or stacked initials) must NOT be merged: their shared x
        position previously produced uniform abs() gaps that looked like a
        clean horizontal comb row and yielded a tall, narrow bogus token."""
        detector = CombBoxDetector()

        elements = [
            LayoutElement(
                id=f"v{i}", text=ch, bbox=[100, 200 + i * 22, 117, 200 + i * 22 + 14],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="docling_ocr", structure_source="docling", reading_order=i
            )
            for i, ch in enumerate("123")
        ]

        is_uniform, _ = detector._is_comb_box_sequence(elements)
        assert is_uniform is False
        assert detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST") == []

    def test_reject_far_apart_same_row_tokens(self):
        """Two short tokens on the same baseline but a half-page apart, with
        nothing between them, must not be merged into one page-spanning token
        (e.g. a label "STD" and a value "PAN" at opposite ends of a line)."""
        detector = CombBoxDetector()

        elements = [
            LayoutElement(
                id="t1", text="STD", bbox=[30, 200, 55, 214],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="docling_ocr", structure_source="docling", reading_order=1
            ),
            LayoutElement(
                id="t2", text="PAN", bbox=[430, 200, 455, 214],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="docling_ocr", structure_source="docling", reading_order=2
            ),
        ]

        is_uniform, _ = detector._is_comb_box_sequence(elements)
        assert is_uniform is False
        assert detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST") == []

    def test_accept_thin_glyph_in_wide_cell(self):
        """Regression: a real comb row keeps its wide printed cell pitch even
        where the ink is a thin glyph ("1"/"I") mid-row. The geometry gate
        must scale its tolerances by the MEDIAN glyph width of the whole
        sequence, not the current element's own width -- otherwise the thin
        glyph's large trailing gap breaks the sequence and the date/ID row
        stops being detected."""
        detector = CombBoxDetector()

        text = "20211105"
        widths = [10, 10, 5, 10, 5, 5, 10, 10]  # the "1" digits are thin ink
        elements = []
        x = 100
        for i, (ch, w) in enumerate(zip(text, widths)):
            elements.append(
                LayoutElement(
                    id=f"d{i}", text=ch, bbox=[x, 200, x + w, 216],
                    page_number=1, type=ElementType.TEXT, confidence=0.85,
                    source="docling_ocr", structure_source="docling", reading_order=i
                )
            )
            x += 22  # wide printed cell pitch, independent of ink width

        is_uniform, _ = detector._is_comb_box_sequence(elements)
        assert is_uniform is True

        merged = detector.detect_and_merge_comb_boxes(elements, page_number=1, doc_id="TEST")
        assert len(merged) == 1
        assert merged[0].text == text


class TestFieldValidator:
    """Test field validation system."""
    
    def test_validate_application_no_valid(self):
        """Test valid application number passes."""
        validator = FieldValidator()
        result = validator.validate_field(
            "application_no",
            "APPL00343265",
            source_type="comb_box_reconstruction"
        )
        
        assert result.is_valid is True
        assert result.confidence >= 0.9
        assert len(result.warnings) == 0
    
    def test_validate_application_no_invalid_length(self):
        """Test application number with wrong length."""
        validator = FieldValidator()
        result = validator.validate_field(
            "application_no",
            "ABC",  # Too short
            source_type="comb_box_reconstruction"
        )
        
        assert result.is_valid is False
        assert result.confidence < 0.5
        assert any("Length" in w for w in result.warnings)
    
    def test_validate_pan_valid(self):
        """Test valid PAN number."""
        validator = FieldValidator()
        result = validator.validate_field(
            "pan_number",
            "CFVPM7810Q"
        )
        
        assert result.is_valid is True
        assert result.confidence >= 0.9
    
    def test_validate_pan_invalid_format(self):
        """Test PAN with invalid format."""
        validator = FieldValidator()
        result = validator.validate_field(
            "pan_number",
            "ABCD123456"  # Wrong format
        )
        
        assert result.is_valid is False
        assert any("pattern" in w.lower() for w in result.warnings)
    
    def test_validate_aadhaar_valid(self):
        """Test valid Aadhaar number."""
        validator = FieldValidator()
        result = validator.validate_field(
            "aadhaar_number",
            "123456789012"
        )
        
        assert result.is_valid is True
        assert result.confidence >= 0.9
    
    def test_validate_bank_account_valid(self):
        """Test valid bank account number."""
        validator = FieldValidator()
        result = validator.validate_field(
            "bank_account_no",
            "987654321012"
        )
        
        assert result.is_valid is True
        assert result.confidence >= 0.9
    
    def test_validate_unknown_field(self):
        """Test field without specific rules."""
        validator = FieldValidator()
        result = validator.validate_field(
            "unknown_field",
            "SomeValue"
        )
        
        # Should accept with default confidence
        assert result.is_valid is True
        assert result.confidence == 0.8
    
    def test_validate_batch(self):
        """Test batch validation."""
        validator = FieldValidator()
        
        fields = {
            "application_no": "APPL00343265",
            "pan_number": "CFVPM7810Q",
            "bank_account_no": "987654321012"
        }
        
        results = validator.validate_batch(fields)
        
        assert len(results) == 3
        assert all(r.is_valid for r in results.values())
    
    def test_validation_summary(self):
        """Test summary statistics."""
        validator = FieldValidator()
        
        results = {
            "field1": ValidationResult(True, 0.95, [], {}),
            "field2": ValidationResult(True, 0.85, [], {}),
            "field3": ValidationResult(False, 0.4, ["error"], {}),
        }
        
        summary = validator.get_validation_summary(results)
        
        assert summary["total_fields"] == 3
        assert summary["valid_count"] == 2
        assert summary["invalid_count"] == 1
        assert summary["validation_rate"] == pytest.approx(0.666, abs=0.01)
    
    def test_strict_mode_rejection(self):
        """Test strict mode rejects invalid fields."""
        validator = FieldValidator(strict_mode=True)
        
        result = validator.validate_field(
            "application_no",
            "ABC",  # Too short
            source_type="comb_box_reconstruction"
        )
        
        assert result.is_valid is False
        assert result.confidence < 0.5


class TestIntegration:
    """Integration tests for end-to-end scenarios."""
    
    def test_full_pipeline_segmented_application_no(self):
        """Test complete pipeline: detection → merging → validation."""
        detector = CombBoxDetector()
        validator = FieldValidator()
        
        # Create segmented application number
        chars = list("APPL00343265")
        elements = []
        x_pos = 100
        for i, char in enumerate(chars):
            elements.append(
                LayoutElement(
                    id=f"e{i}", text=char, bbox=[x_pos, 200, x_pos + 10, 210],
                    page_number=1, type=ElementType.TEXT, confidence=0.9,
                    source="docling_ocr", structure_source="docling", reading_order=i
                )
            )
            x_pos += 15
        
        # Step 1: Detect and merge
        merged_tokens = detector.detect_and_merge_comb_boxes(
            elements, page_number=1, doc_id="TEST"
        )
        
        assert len(merged_tokens) == 1
        merged = merged_tokens[0]
        
        # Step 2: Validate
        validation = validator.validate_field(
            "application_no",
            merged.text,
            source_type=merged.source_type
        )
        
        assert validation.is_valid is True
        assert validation.confidence >= 0.9
        assert merged.text == "APPL00343265"
    
    def test_mixed_content_page(self):
        """Test page with both comb-box and normal text."""
        detector = CombBoxDetector()
        
        # Mix of segmented field and normal text
        elements = []
        
        # Normal text (long)
        elements.append(
            LayoutElement(
                id="normal1", text="Applicant Name:", bbox=[100, 100, 200, 110],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="docling_ocr", structure_source="docling", reading_order=1
            )
        )
        
        # Segmented field
        x_pos = 100
        for i, char in enumerate("APPL00343265"):
            elements.append(
                LayoutElement(
                    id=f"seg{i}", text=char, bbox=[x_pos, 200, x_pos + 10, 210],
                    page_number=1, type=ElementType.TEXT, confidence=0.9,
                    source="docling_ocr", structure_source="docling", reading_order=10+i
                )
            )
            x_pos += 15
        
        # Normal text (long)
        elements.append(
            LayoutElement(
                id="normal2", text="Loan Amount:", bbox=[100, 300, 200, 310],
                page_number=1, type=ElementType.TEXT, confidence=0.9,
                source="docling_ocr", structure_source="docling", reading_order=50
            )
        )
        
        merged = detector.detect_and_merge_comb_boxes(
            elements, page_number=1, doc_id="TEST"
        )
        
        # Should only merge the segmented field
        assert len(merged) == 1
        assert merged[0].text == "APPL00343265"
    
    def test_multiple_comb_boxes_same_row(self):
        """Test multiple segmented fields on same row."""
        detector = CombBoxDetector()
        
        elements = []
        
        # First field: "APPL"
        x_pos = 100
        for i, char in enumerate("APPL"):
            elements.append(
                LayoutElement(
                    id=f"field1_{i}", text=char, bbox=[x_pos, 200, x_pos + 10, 210],
                    page_number=1, type=ElementType.TEXT, confidence=0.9,
                    source="docling_ocr", structure_source="docling", reading_order=i
                )
            )
            x_pos += 15
        
        # Large gap (breaks sequence)
        # Second field: "1234"
        x_pos = 300
        for i, char in enumerate("1234"):
            elements.append(
                LayoutElement(
                    id=f"field2_{i}", text=char, bbox=[x_pos, 200, x_pos + 10, 210],
                    page_number=1, type=ElementType.TEXT, confidence=0.9,
                    source="docling_ocr", structure_source="docling", reading_order=10+i
                )
            )
            x_pos += 15
        
        merged = detector.detect_and_merge_comb_boxes(
            elements, page_number=1, doc_id="TEST"
        )
        
        # Should detect 2 separate fields
        assert len(merged) == 2
        texts = sorted([m.text for m in merged])
        assert "1234" in texts
        assert "APPL" in texts
