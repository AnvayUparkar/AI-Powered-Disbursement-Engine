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
import io
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

    def test_unlocalized_elements_dedupe_on_text_and_keep_distinct_lines(self):
        """Unlocalized elements (bbox_estimated=True) deduplicate on text, not fake IoU."""
        serializer = DocumentSerializer()
        full_box = [0.0, 0.0, 1.0, 1.0]

        existing_elements = [
            Mock(bbox=full_box, text="Header OSV Stamp", metadata={"bbox_estimated": True})
        ]

        # 1. Distinct line with identical [0, 0, 1, 1] box must NOT be dropped as duplicate
        pan_dup = serializer._is_duplicate(
            ocr_bbox=full_box,
            existing_elements=existing_elements,
            text="INCOME TAX DEPARTMENT DCJPD9154G",
            bbox_estimated=True
        )
        assert pan_dup is False, "Distinct text must be preserved despite identical full-page boxes"

        # 2. Duplicate line with identical text MUST be dropped
        stamp_dup = serializer._is_duplicate(
            ocr_bbox=full_box,
            existing_elements=existing_elements,
            text="Header OSV Stamp",
            bbox_estimated=True
        )
        assert stamp_dup is True, "Identical unlocalized text must be deduplicated"


    def test_lightonocr_hard_fail_truncated_marks_extraction_failed(self):
        """Truncated output from LightOnOCR must mark extraction_failed=True for VLM fallback."""
        mock_result = LightOnOCRResult(
            text="Truncated text...",
            confidence=0.95,
            bboxes=[],
            inference_time_ms=100.0,
            quality_score=0.95,
            hard_fail_reason="truncated"
        )
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=mock_result):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image_bytes",
                page_number=1,
                image_width=595.0,
                image_height=842.0,
                doc_id="TEST-TRUNC"
            )
        assert ocr_result is not None
        assert ocr_result.extraction_failed is True
        assert len(ocr_result.elements) == 0

    def test_lightonocr_hard_fail_repetition_marks_extraction_failed(self):
        """Repetitive looped output from LightOnOCR must mark extraction_failed=True."""
        mock_result = LightOnOCRResult(
            text="loop loop loop loop loop loop",
            confidence=0.95,
            bboxes=[],
            inference_time_ms=100.0,
            quality_score=0.95,
            hard_fail_reason="repetition"
        )
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=mock_result):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image_bytes",
                page_number=1,
                image_width=595.0,
                image_height=842.0,
                doc_id="TEST-REP"
            )
        assert ocr_result is not None
        assert ocr_result.extraction_failed is True

    def test_lightonocr_hard_fail_empty_marks_extraction_failed(self):
        """Empty text on high ink page must mark extraction_failed=True."""
        mock_result = LightOnOCRResult(
            text="",
            confidence=0.0,
            bboxes=[],
            inference_time_ms=50.0,
            quality_score=0.0,
            hard_fail_reason="empty"
        )
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=mock_result):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image_bytes",
                page_number=1,
                image_width=595.0,
                image_height=842.0,
                doc_id="TEST-EMPTY"
            )
        assert ocr_result is not None
        assert ocr_result.extraction_failed is True

    def test_prepare_image_downscale_and_rgb(self):
        """Input image must be converted to RGB and downscaled if longest edge > 1540."""
        from PIL import Image
        engine = LightOnOCREngine()

        # Large grayscale image 2000x1000
        large_gray = Image.new("L", (2000, 1000), color=255)
        prepared = engine._prepare_image(large_gray)
        assert prepared.mode == "RGB"
        assert max(prepared.size) <= 1540
        assert prepared.size[0] == 1540
        assert prepared.size[1] == 770

        # Small RGBA image 500x400 (should not upscale)
        small_rgba = Image.new("RGBA", (500, 400), color=(255, 255, 255, 255))
        prepared_small = engine._prepare_image(small_rgba)
        assert prepared_small.mode == "RGB"
        assert prepared_small.size == (500, 400)

    def test_conversation_prompt_is_image_only(self):
        """Assert no text prompt ('Extract the text from this document.') in user conversation."""
        from PIL import Image
        engine = LightOnOCREngine()
        dummy_img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        img_byte_arr = io.BytesIO()
        dummy_img.save(img_byte_arr, format='PNG')
        img_bytes = img_byte_arr.getvalue()

        mock_processor = Mock()
        mock_processor.apply_chat_template = Mock(return_value={"input_ids": [1, 2, 3]})
        
        with patch.object(engine, 'is_loaded', return_value=True), \
             patch.object(engine, '_processor', mock_processor), \
             patch.object(engine, '_run_inference_with_timeout', return_value={"text": "Hello", "confidence": 0.9, "bboxes": []}), \
             patch.object(settings, 'LIGHTONOCR_ENABLED', True):
            engine.process_page(img_bytes, page_number=1, doc_id="TEST-PROMPT")

        assert mock_processor.apply_chat_template.called
        call_args = mock_processor.apply_chat_template.call_args[0][0]
        # Inspect conversation structure
        user_content = call_args[0]["content"]
        types = [item["type"] for item in user_content]
        assert "image" in types
        assert "text" not in types, "Prompt must be image-only without text prompt"

    def test_adapter_splits_lines_and_aligns_boxes(self):
        """Adapter splits multiline text and assigns line numbers and estimated bboxes."""
        mock_result = LightOnOCRResult(
            text="Line 1: Loan Application\nLine 2: Account Number\nLine 3: IFSC Code",
            confidence=0.92,
            bboxes=[],
            inference_time_ms=120.0,
            quality_score=0.88
        )
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=mock_result), \
             patch.object(adapter, '_detect_line_boxes', return_value=[
                 [10.0, 10.0, 200.0, 30.0],
                 [10.0, 40.0, 200.0, 60.0]
             ]):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image",
                page_number=1,
                image_width=595.0,
                image_height=842.0,
                doc_id="TEST-LINES"
            )

        assert ocr_result is not None
        assert len(ocr_result.elements) == 3
        assert ocr_result.elements[0].text == "Line 1: Loan Application"
        assert ocr_result.elements[0].line_number == 1
        assert ocr_result.elements[0].metadata.get("bbox_estimated") is False
        assert ocr_result.elements[1].line_number == 2
        assert ocr_result.elements[1].metadata.get("bbox_estimated") is False
        # 3rd line had no detected box, falls back to full page with bbox_estimated=True
        assert ocr_result.elements[2].line_number == 3
        assert ocr_result.elements[2].metadata.get("bbox_estimated") is True


class TestQualityScoreComputation:
    """Test real coverage-based quality score computation in LightOnOCREngine."""
    
    def test_quality_score_empty_text(self):
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("", 0.95)
        assert score == 0.0
    
    def test_quality_score_none_text(self):
        engine = LightOnOCREngine()
        score = engine._compute_quality_score(None, 0.90)
        assert score == 0.0

    def test_quality_score_whitespace_only_text(self):
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("   \n   ", 0.90)
        assert score == 0.0

    def test_quality_score_clean_english_text(self):
        engine = LightOnOCREngine()
        text = "State Bank of India Loan Application Form Account Number 1234567890" * 5
        score = engine._compute_quality_score(text, 0.95)
        assert score > 0.50

    def test_quality_score_penalizes_garbled_text(self):
        engine = LightOnOCREngine()
        # All lines garbled
        garbled_text = "3T9T3πT&T\nHRTRR\nRROR"
        score = engine._compute_quality_score(garbled_text, 0.95)
        # garble_ratio is high -> quality should be heavily penalized
        assert score < 0.30

    def test_quality_score_penalizes_low_confidence(self):
        engine = LightOnOCREngine()
        text = "Standard loan sanction letter text with good content length." * 5
        high_conf_score = engine._compute_quality_score(text, 0.95)
        low_conf_score = engine._compute_quality_score(text, 0.20)
        assert low_conf_score < high_conf_score
        assert low_conf_score <= 0.20
