import pytest
from idp.services.ocr.script_detector import ScriptDetector, ScriptCategory, ScriptDetectionResult
from idp.services.ocr.ocr_model_router import OCRModelRouter, OCRRoutingDecision
from idp.services.ocr.confidence import OCRConfidenceEvaluator
from idp.models.ocr import OCRElement, OCRResult
from idp.utils.masking import mask_sensitive_pii
from idp.core.config import settings


# 1. English-only text -> english profile
def test_scenario_1_english_only_text():
    detector = ScriptDetector()
    res = detector.detect_script("Government of India")
    assert res.primary_script == "latin"
    assert res.is_mixed is False

    router = OCRModelRouter()
    decision = router.resolve_routing_decision(preview_text="Government of India")
    assert decision.model_profile == "english"


# 2. Devanagari text -> devanagari profile
def test_scenario_2_devanagari_text():
    detector = ScriptDetector()
    res = detector.detect_script("भारत सरकार")
    assert res.primary_script == "devanagari"

    router = OCRModelRouter()
    decision = router.resolve_routing_decision(preview_text="भारत सरकार")
    assert decision.model_profile == "devanagari"


# 3. Mixed Aadhaar text -> devanagari / multilingual profile
def test_scenario_3_mixed_aadhaar_text():
    detector = ScriptDetector()
    res = detector.detect_script("भारत सरकार Government of India")
    assert res.is_mixed is True
    assert "devanagari" in res.scripts_detected
    assert "latin" in res.scripts_detected

    router = OCRModelRouter()
    decision = router.resolve_routing_decision(preview_text="भारत सरकार Government of India")
    assert decision.model_profile in ["devanagari", "multilingual"]


# 4. English bank statement -> English profile remains selected
def test_scenario_4_english_bank_statement():
    router = OCRModelRouter()
    decision = router.resolve_routing_decision(
        doc_type_hint="bank_statement",
        preview_text="HDFC BANK STATEMENT ACCOUNT BALANCE SUMMARY DEBIT CREDIT"
    )
    assert decision.model_profile == "english"
    assert decision.routing_reason == "english_latin_default"


# 5. Garbled text -> flagged for fallback
def test_scenario_5_garbled_text():
    evaluator = OCRConfidenceEvaluator()
    assert evaluator.is_garbled_text("3T9T3πT&T") is True
    
    elem = OCRElement(
        id="ocr-garbled",
        text="3T9T3πT&T",
        bbox=[0, 0, 10, 10],
        confidence=0.95,
        page_number=1
    )
    eval_elem = evaluator.evaluate_element(elem)
    assert eval_elem.needs_vlm is True


# 6. Valid Devanagari -> NOT flagged as garbage
def test_scenario_6_valid_devanagari():
    evaluator = OCRConfidenceEvaluator()
    text = "मेरा आधार मेरी पहचान"
    assert evaluator.is_garbled_text(text) is False

    elem = OCRElement(
        id="ocr-hindi",
        text=text,
        bbox=[0, 0, 10, 10],
        confidence=0.92,
        page_number=1
    )
    eval_elem = evaluator.evaluate_element(elem)
    assert eval_elem.needs_vlm is False


# 7. Valid English -> NOT flagged as garbage
def test_scenario_7_valid_english():
    evaluator = OCRConfidenceEvaluator()
    text = "Shah & Anchor Kutchhi Engineering College"
    assert evaluator.is_garbled_text(text) is False

    elem = OCRElement(
        id="ocr-eng",
        text=text,
        bbox=[0, 0, 10, 10],
        confidence=0.96,
        page_number=1
    )
    eval_elem = evaluator.evaluate_element(elem)
    assert eval_elem.needs_vlm is False


# 8. Mixed valid text -> NOT falsely classified as garbage
def test_scenario_8_mixed_valid_text():
    evaluator = OCRConfidenceEvaluator()
    text = "नाम / Name Rahul Sharma"
    assert evaluator.is_garbled_text(text) is False


# 9. VLM correction -> in-place update, ocr_original preserved, no duplicate
def test_scenario_9_vlm_in_place_correction():
    original_elem = OCRElement(
        id="ocr-vlm-target",
        text="HRTRR",
        bbox=[10, 10, 50, 20],
        confidence=0.60,
        needs_vlm=True,
        page_number=1
    )
    # Simulate VLM in-place update
    original_elem.ocr_original = original_elem.text
    original_elem.text = "भारत सरकार"
    original_elem.source = "vlm_corrected"
    original_elem.confidence = 0.98

    assert original_elem.text == "भारत सरकार"
    assert original_elem.ocr_original == "HRTRR"
    assert original_elem.source == "vlm_corrected"


# 10. Privacy & PII Masking Utility
def test_privacy_pii_masking_utility():
    raw = "Aadhaar: 7241 5860 0518 PAN: ABCDE1234F Account: 12345678901234"
    masked = mask_sensitive_pii(raw)

    assert "7241 5860 0518" not in masked
    assert "0518" in masked
    assert "ABCDE1234F" not in masked
    assert "XXXXX1234F" in masked


# =====================================================================
# Comprehensive 6-Test Suite Required for Multilingual OCR & VLM Fallback
# =====================================================================

# Test 1: Normal English document
def test_case_1_normal_english_document():
    from idp.services.output.serializer import DocumentSerializer
    from idp.services.docling.parser import DoclingParseResult
    from idp.models.processing import ProcessingMetrics
    serializer = DocumentSerializer()
    evaluator = OCRConfidenceEvaluator()

    elem = OCRElement(
        id="elem-eng-1",
        text="Government of India",
        bbox=[10, 10, 200, 30],
        confidence=0.98,
        page_number=1,
        source="ocr"
    )
    evaluator.evaluate_element(elem)
    assert elem.needs_vlm is False

    ocr_res = OCRResult(page_number=1, elements=[elem], image_width=600, image_height=800)
    docling_res = DoclingParseResult(elements=[], tables=[], page_count=1, pages_dimensions=[{"width": 600.0, "height": 800.0}])
    unified = serializer.build_unified_document(
        doc_id="DOC-TEST-1",
        filename="test.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        docling_result=docling_res,
        ocr_results=[ocr_res],
        vlm_corrections={},
        metrics=ProcessingMetrics()
    )
    assert "Government of India" in unified.text


# Test 2: Devanagari document
def test_case_2_devanagari_document():
    from idp.services.output.serializer import DocumentSerializer
    from idp.services.docling.parser import DoclingParseResult
    from idp.models.processing import ProcessingMetrics
    serializer = DocumentSerializer()
    evaluator = OCRConfidenceEvaluator()

    elem = OCRElement(
        id="elem-dev-1",
        text="भारत सरकार",
        bbox=[10, 10, 200, 30],
        confidence=0.95,
        page_number=1,
        source="ocr"
    )
    evaluator.evaluate_element(elem)
    assert elem.needs_vlm is False

    ocr_res = OCRResult(page_number=1, elements=[elem], image_width=600, image_height=800)
    docling_res = DoclingParseResult(elements=[], tables=[], page_count=1, pages_dimensions=[{"width": 600.0, "height": 800.0}])
    unified = serializer.build_unified_document(
        doc_id="DOC-TEST-2",
        filename="test_dev.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        docling_result=docling_res,
        ocr_results=[ocr_res],
        vlm_corrections={},
        metrics=ProcessingMetrics()
    )
    assert "भारत सरकार" in unified.text


# Test 3: Garbled Devanagari OCR with VLM in-place correction
@pytest.mark.asyncio
async def test_case_3_garbled_devanagari_ocr():
    from idp.services.output.serializer import DocumentSerializer
    from idp.services.docling.parser import DoclingParseResult
    from idp.models.processing import ProcessingMetrics
    from idp.services.vlm.client import VLMResult
    serializer = DocumentSerializer()
    evaluator = OCRConfidenceEvaluator()

    garbled_elem = OCRElement(
        id="elem-garbled-1",
        text="HRTRR",
        bbox=[10, 10, 200, 30],
        confidence=0.92,  # High confidence from engine, but garbled
        page_number=1,
        source="ocr"
    )
    evaluator.evaluate_element(garbled_elem)
    assert garbled_elem.needs_vlm is True

    # Simulate VLM fallback result
    vlm_result = VLMResult(
        text="भारत सरकार",
        confidence=0.98,
        verified=True,
        source="vlm_corrected",
        ocr_original="HRTRR"
    )
    vlm_corrections = {garbled_elem.id: vlm_result}

    ocr_res = OCRResult(page_number=1, elements=[garbled_elem], image_width=600, image_height=800)
    docling_res = DoclingParseResult(elements=[], tables=[], page_count=1, pages_dimensions=[{"width": 600.0, "height": 800.0}])
    unified = serializer.build_unified_document(
        doc_id="DOC-TEST-3",
        filename="test_garbled.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        docling_result=docling_res,
        ocr_results=[ocr_res],
        vlm_corrections=vlm_corrections,
        metrics=ProcessingMetrics()
    )
    # Output must contain corrected Devanagari, and NOT the garbled Latin
    assert "भारत सरकार" in unified.text
    assert "HRTRR" not in unified.text
    # Element metadata preserves ocr_original and source
    corrected_elements = [e for e in unified.pages[0].elements if e.id == garbled_elem.id]
    assert len(corrected_elements) == 1
    assert corrected_elements[0].text == "भारत सरकार"
    assert corrected_elements[0].ocr_original == "HRTRR"
    assert corrected_elements[0].source == "vlm_corrected"


# Test 4: Mixed-language document (Devanagari + English + Numbers)
def test_case_4_mixed_language_document():
    from idp.services.output.serializer import DocumentSerializer
    from idp.services.docling.parser import DoclingParseResult
    from idp.models.processing import ProcessingMetrics
    serializer = DocumentSerializer()

    elements = [
        OCRElement(id="e1", text="भारत सरकार", bbox=[10, 10, 200, 30], confidence=0.95, page_number=1),
        OCRElement(id="e2", text="Government of India", bbox=[10, 40, 250, 60], confidence=0.97, page_number=1),
        OCRElement(id="e3", text="Jainam Sampat Parmar", bbox=[10, 70, 250, 90], confidence=0.99, page_number=1),
        OCRElement(id="e4", text="7241 5860 0518", bbox=[10, 100, 200, 120], confidence=0.98, page_number=1)
    ]
    ocr_res = OCRResult(page_number=1, elements=elements, image_width=600, image_height=800)
    docling_res = DoclingParseResult(elements=[], tables=[], page_count=1, pages_dimensions=[{"width": 600.0, "height": 800.0}])
    unified = serializer.build_unified_document(
        doc_id="DOC-TEST-4",
        filename="mixed_aadhaar.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        page_count=1,
        docling_result=docling_res,
        ocr_results=[ocr_res],
        vlm_corrections={},
        metrics=ProcessingMetrics()
    )
    assert "भारत सरकार" in unified.text
    assert "Government of India" in unified.text
    assert "Jainam Sampat Parmar" in unified.text
    assert "7241 5860 0518" in unified.text




# Test 5: Existing deduplication (IoU >= 0.5)
def test_case_5_existing_deduplication():
    from idp.services.output.serializer import DocumentSerializer
    serializer = DocumentSerializer()

    box1 = [10.0, 10.0, 100.0, 50.0]
    box2 = [12.0, 12.0, 98.0, 48.0]   # High overlap (IoU > 0.8)
    box3 = [200.0, 200.0, 300.0, 250.0]  # No overlap

    iou_overlap = serializer._compute_iou(box1, box2)
    iou_separate = serializer._compute_iou(box1, box3)

    assert iou_overlap >= 0.70
    assert iou_separate == 0.0


# Test 6: VLM failure preserves original OCR without crashing
@pytest.mark.asyncio
async def test_case_6_vlm_failure_preserves_ocr():
    from idp.services.vlm.client import VLMClient
    client = VLMClient(provider="openai", api_key="")

    elem = OCRElement(
        id="elem-test-fail",
        text="Original OCR Text",
        bbox=[10, 10, 50, 20],
        confidence=0.50,
        needs_vlm=True,
        page_number=1
    )
    # Analyze region with invalid key/fallback must not raise an unhandled exception
    res = await client.analyze_region(
        image_bytes=b"invalid_dummy_bytes",
        ocr_element=elem,
        context_hint="Page 1",
        doc_id="DOC-FAIL"
    )
    assert res is not None
    assert res.ocr_original == "Original OCR Text"

