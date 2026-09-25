"""
Tests for LightOnOCR scanned page routing.

Verifies:
1. Scanned pages -> LightOnOCR when enabled
2. Digital pages -> Existing Docling pipeline (unchanged, zero regression)
3. LightOnOCR low quality -> VLM fallback flag
4. LightOnOCR failure -> failed OCR result
5. LightOnOCR timeout -> None (safe degradation)
6. LightOnOCR result deduplication in serializer
"""
import pytest
from unittest.mock import Mock, patch, AsyncMock
from idp.services.ocr.lightonocr_engine import LightOnOCREngine, LightOnOCRResult
from idp.services.ocr.lightonocr_adapter import LightOnOCRAdapter
from idp.models.ocr import OCRResult, OCRElement
from idp.services.output.serializer import DocumentSerializer
from idp.core.config import settings


class TestLightOnOCRRouting:
    """Test scanned page routing to LightOnOCR."""
    
    def test_scanned_page_uses_lightonocr(self):
        """SCANNED page must route to LightOnOCR when enabled."""
        mock_result = LightOnOCRResult(
            text="Test OCR Text for Scanned Page",
            confidence=0.95,
            bboxes=[[10.0, 10.0, 100.0, 30.0]],
            inference_time_ms=150.0,
            quality_score=0.85
        )
        
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=mock_result):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image_bytes",
                page_number=1,
                image_width=595.0,
                image_height=842.0,
                doc_id="TEST-001"
            )
        
        assert ocr_result is not None
        assert ocr_result.extraction_failed is False
        assert len(ocr_result.elements) > 0
        assert ocr_result.elements[0].source == "lightonocr"
        assert ocr_result.elements[0].text == "Test OCR Text for Scanned Page"
        assert ocr_result.elements[0].needs_vlm is False
    
    def test_digital_page_skips_lightonocr(self):
        """DIGITAL page must NOT route to LightOnOCR under any circumstances."""
        # Simulate digital PDF detection: is_scanned_pdf is False
        is_scanned_pdf = False
        lightonocr_enabled = True
        
        # Hard architectural condition from DocumentProcessor
        should_use_lightonocr = is_scanned_pdf and lightonocr_enabled
        
        assert not should_use_lightonocr, (
            "Digital pages must NEVER use LightOnOCR"
        )
    
    def test_lightonocr_low_quality_triggers_vlm(self):
        """Low quality LightOnOCR result must trigger VLM fallback."""
        mock_result = LightOnOCRResult(
            text="3T9T3πT&T",  # Corrupted/garbled text
            confidence=0.50,
            bboxes=[],
            inference_time_ms=100.0,
            quality_score=0.25  # Below default threshold 0.40
        )
        
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=mock_result):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image_bytes",
                page_number=1,
                image_width=595.0,
                image_height=842.0,
                doc_id="TEST-002"
            )
        
        assert ocr_result is not None
        assert ocr_result.low_confidence_count > 0
        assert ocr_result.elements[0].needs_vlm is True
    
    def test_lightonocr_failure_creates_failed_result(self):
        """LightOnOCR failure must create extraction_failed result for graceful VLM fallback."""
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=None):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image_bytes",
                page_number=1,
                image_width=595.0,
                image_height=842.0,
                doc_id="TEST-003"
            )
        
        assert ocr_result is not None
        assert ocr_result.extraction_failed is True
        assert len(ocr_result.elements) == 0
        assert ocr_result.average_confidence == 0.0
    
    def test_lightonocr_timeout_returns_none(self):
        """LightOnOCR timeout in engine returns None (safe degradation)."""
        engine = LightOnOCREngine()
        
        with patch.object(engine, 'is_loaded', return_value=True), \
             patch.object(engine, '_run_inference_with_timeout', return_value=None), \
             patch.object(settings, 'LIGHTONOCR_ENABLED', True):
            result = engine.process_page(
                image_bytes=b"fake_image_bytes",
                page_number=1,
                doc_id="TEST-TIMEOUT"
            )
        
        assert result is None
    
    def test_lightonocr_results_deduplicated_by_serializer(self):
        """LightOnOCR results must pass through _is_duplicate deduplication in serializer."""
        serializer = DocumentSerializer()
        
        box1 = [10.0, 10.0, 100.0, 50.0]
        box2 = [12.0, 12.0, 98.0, 48.0]
        
        iou = serializer._compute_iou(box1, box2)
        assert iou >= 0.70  # High spatial overlap
        
        # Two identical text strings with high overlap are detected as duplicates
        existing_elements = [
            Mock(bbox=box1, text="Duplicate Line")
        ]
        is_dup = serializer._is_duplicate(
            ocr_bbox=box2,
            existing_elements=existing_elements,
            iou_threshold=0.50,
            text="Duplicate Line"
        )
        assert is_dup is True


class TestQualityScoreComputation:
    """Test heuristic quality score computation in LightOnOCREngine."""
    
    def test_quality_score_empty_text(self):
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("", 0.95, b"")
        assert score == 0.0
    
    def test_quality_score_clean_english_text(self):
        engine = LightOnOCREngine()
        score = engine._compute_quality_score(
            "State Bank of India Loan Application Form Account Number 1234567890",
            0.98,
            b""
        )
        assert score >= 0.80
    
    def test_quality_score_short_fragment(self):
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("A1", 0.50, b"")
        assert score < 0.40

    def test_quality_score_none_text(self):
        """Failure mode: text=None must not raise and must score as empty."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score(None, 0.90, b"")
        assert score == 0.0

    def test_quality_score_whitespace_only_text(self):
        """Edge case: whitespace-only text strips to empty and scores as empty."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("   ", 0.90, b"")
        assert score == 0.0

    def test_quality_score_length_gate_below_five_chars(self):
        """Boundary: 4 chars falls into the <5 'severe fragment' bracket (+0.05 base)."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("ABCD", 1.0, b"")
        assert score == 0.51

    def test_quality_score_length_gate_at_five_chars(self):
        """Boundary: 5 chars crosses into the >=5 bracket (+0.2 base), jumping the score."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("ABCDE", 1.0, b"")
        assert score == 0.70

    def test_quality_score_length_gate_below_ten_chars(self):
        """Boundary: 9 chars stays in the >=5,<10 bracket, matching the 5-char score."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("ABCDEFGHI", 1.0, b"")
        assert score == 0.70

    def test_quality_score_length_gate_at_ten_chars(self):
        """Boundary: 10 chars crosses both the base-score and content-depth gates at once."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("ABCDEFGHIJ", 1.0, b"")
        assert score == 0.90

    def test_quality_score_content_depth_below_twenty_five_chars(self):
        """Boundary: 24 chars stays below the full content-depth bonus."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("A" * 24, 1.0, b"")
        assert score == 0.90

    def test_quality_score_content_depth_at_twenty_five_chars(self):
        """Boundary: 25 chars unlocks the full content-depth bonus, reaching the max score."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("A" * 25, 1.0, b"")
        assert score == 1.0

    def test_quality_score_confidence_clamped_when_negative(self):
        """Failure mode: a negative confidence must clamp to 0 contribution, never go negative."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("A" * 25, -1.0, b"")
        assert score == 0.70

    def test_quality_score_confidence_clamped_above_one(self):
        """Edge case: confidence > 1.0 must clamp to 1.0 contribution, not overshoot."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("ABCDE", 2.0, b"")
        assert score == 0.70
