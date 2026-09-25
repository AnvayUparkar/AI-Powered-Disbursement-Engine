from idp.models.ocr import OCRElement, OCRResult
from idp.services.ocr.confidence import OCRConfidenceEvaluator
from idp.services.vlm.router import ConfidenceRouter
import pytest
from unittest.mock import MagicMock


def test_confidence_evaluation_and_router():
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    router = ConfidenceRouter(threshold=0.70, vlm_enabled=True)

    high_conf_elem = OCRElement(
        id="1", text="Clean Text", bbox=[0, 0, 10, 10], confidence=0.95, page_number=1
    )
    low_conf_elem = OCRElement(
        id="2", text="~~~~~", bbox=[0, 0, 10, 10], confidence=0.40, page_number=1
    )

    result = OCRResult(page_number=1, elements=[high_conf_elem, low_conf_elem])
    evaluated = evaluator.evaluate_result(result)

    assert evaluated.low_confidence_count == 1
    assert router.should_use_vlm(evaluated) is True
    assert len(router.get_low_confidence_elements(evaluated)) == 1


def test_compute_text_confidence_clean_inputs():
    evaluator = OCRConfidenceEvaluator(threshold=0.80)

    # Standard clean titles and names must have high confidence (>= 0.95)
    assert evaluator.compute_text_confidence("Mr.") >= 0.95
    assert evaluator.compute_text_confidence("Dr.") >= 0.95
    assert evaluator.compute_text_confidence("Spouse Name Title") >= 0.95
    assert evaluator.compute_text_confidence("GYAN CHAND KHATRI") >= 0.95
    assert evaluator.compute_text_confidence("Loan Application Form") >= 0.95


def test_compute_text_confidence_ocr_artifacts():
    evaluator = OCRConfidenceEvaluator(threshold=0.80)

    # 1. Clipped leading letter + glued casing (e.g. "pplicant NamePRAKASHKHATRI")
    score_clipped = evaluator.compute_text_confidence("pplicant NamePRAKASHKHATRI")
    assert score_clipped < 0.80, f"Expected < 0.80 for clipped/glued text, got {score_clipped}"

    # 2. Sandwiched lowercase inside uppercase (e.g. "Mother's Name KAmLA")
    score_sandwiched = evaluator.compute_text_confidence("Mother's Name KAmLA")
    assert score_sandwiched < 0.85, f"Expected < 0.85 for sandwiched casing, got {score_sandwiched}"
    assert score_sandwiched < evaluator.compute_text_confidence("Mother's Name KAMLA")

    # 3. Digit inside word (e.g. "L0AN F0RM")
    score_digit = evaluator.compute_text_confidence("L0AN F0RM")
    assert score_digit < 0.85, f"Expected < 0.85 for digit in word, got {score_digit}"


def test_compute_text_confidence_edge_cases():
    evaluator = OCRConfidenceEvaluator(threshold=0.80)

    # Empty and whitespace
    assert evaluator.compute_text_confidence("") == 0.0
    assert evaluator.compute_text_confidence("   ") == 0.0

    # Garbled noise
    assert evaluator.compute_text_confidence("~~~~~") <= 0.40
    assert evaluator.compute_text_confidence("HRTRR") <= 0.40
    assert evaluator.compute_text_confidence("!@#$%^&*") <= 0.40

    # Degenerate zero-size bounding box
    score_normal = evaluator.compute_text_confidence("Valid Text", bbox=[10.0, 10.0, 100.0, 50.0])
    score_zero_bbox = evaluator.compute_text_confidence("Valid Text", bbox=[10.0, 10.0, 10.0, 10.0])
    assert score_zero_bbox < score_normal


def _mock_bbox(l, t, r, b):
    """A bbox stand-in exposing only .l/.t/.r/.b, matching _extract_top_left_bbox's
    second branch (no to_top_left_origin -> treated as already top-left-origin)."""
    bbox = MagicMock()
    bbox.l, bbox.t, bbox.r, bbox.b = l, t, r, b
    # hasattr(bbox, "to_top_left_origin") must be False for a plain MagicMock spec'd
    # to only expose l/t/r/b.
    del bbox.to_top_left_origin
    return bbox


def test_docling_parser_reads_real_model_scores_not_a_text_heuristic(monkeypatch):
    """DoclingParser must report the layout model's and RapidOCR's own per-cluster/per-cell
    confidence -- not a rule-based guess derived from the text's shape. Two elements with
    identical, clean-looking text must still get different scores when their underlying
    model-reported scores differ, and an element with no overlapping cluster must get an
    honest 1.0 (unmeasured) rather than a fabricated estimate."""
    from idp.services.docling.parser import DoclingParser
    from unittest.mock import MagicMock

    parser = DoclingParser()

    class MockItem:
        def __init__(self, text, bbox, page_no=1, label="text"):
            self.text = text
            self.label = label
            prov = MagicMock()
            prov.page_no = page_no
            prov.bbox = bbox
            prov.confidence = None
            self.prov = [prov]

    class MockDoc:
        def __init__(self):
            self.pages = {1: MagicMock(size=MagicMock(width=595.0, height=842.0))}
            # Same text, different underlying model confidence per element.
            self.texts = [
                MockItem("Spouse Name Title", _mock_bbox(0, 0, 100, 20)),
                MockItem("Spouse Name Title", _mock_bbox(0, 100, 100, 120)),
                MockItem("No matching cluster", _mock_bbox(500, 500, 600, 520)),
            ]
            self.tables = []

    def _mock_cluster(bbox, layout_conf, cell_confs):
        cl = MagicMock()
        cl.bbox = bbox
        cl.confidence = layout_conf
        cl.cells = [MagicMock(confidence=c) for c in cell_confs]
        return cl

    conv_page = MagicMock()
    conv_page.page_no = 1
    conv_page.size = MagicMock(width=595.0, height=842.0)
    conv_page.predictions.layout.clusters = [
        # Overlaps element 0 exactly: high-confidence cluster.
        _mock_cluster(_mock_bbox(0, 0, 100, 20), layout_conf=0.98, cell_confs=[0.97, 0.99]),
        # Overlaps element 1 exactly: low-confidence cluster (e.g. faint scan).
        _mock_cluster(_mock_bbox(0, 100, 100, 120), layout_conf=0.55, cell_confs=[0.40, 0.45]),
        # Nowhere near element 2 -- no overlap.
        _mock_cluster(_mock_bbox(300, 300, 320, 310), layout_conf=0.90, cell_confs=[0.90]),
    ]

    mock_converter = MagicMock()
    mock_conv_result = MagicMock()
    mock_conv_result.document = MockDoc()
    mock_conv_result.pages = [conv_page]
    mock_converter.convert.return_value = mock_conv_result

    monkeypatch.setattr(parser.pipeline, "get_converter", lambda: mock_converter)

    result = parser.parse("dummy.pdf", doc_id="TEST-DOC")

    assert len(result.elements) == 3
    high, low, unmatched = result.elements

    # Real per-model scores are surfaced separately from the blended `confidence`.
    assert high.layout_confidence == pytest.approx(0.98)
    assert high.ocr_confidence == pytest.approx(0.98, abs=0.01)
    assert low.layout_confidence == pytest.approx(0.55)
    assert low.ocr_confidence == pytest.approx(0.425, abs=0.01)

    # Identical text, different real scores -> different confidence. A text-shape
    # heuristic would have scored these two identically.
    assert high.confidence != low.confidence
    assert high.confidence > low.confidence

    # No overlapping cluster -> honest "not measured" (1.0), never a guessed value.
    assert unmatched.ocr_confidence is None
    assert unmatched.layout_confidence is None
    assert unmatched.confidence == 1.0


def _build_mock_conv_result(page_count: int = 1):
    """Minimal Docling convert() result: `page_count` clean pages, no tables, matching the
    fixture shape used by test_docling_parser_reads_real_model_scores_not_a_text_heuristic."""
    from unittest.mock import MagicMock

    class MockItem:
        def __init__(self, text, bbox, page_no=1, label="text"):
            self.text = text
            self.label = label
            prov = MagicMock()
            prov.page_no = page_no
            prov.bbox = bbox
            prov.confidence = None
            self.prov = [prov]

    class MockDoc:
        def __init__(self):
            self.pages = {
                p: MagicMock(size=MagicMock(width=595.0, height=842.0))
                for p in range(1, page_count + 1)
            }
            self.texts = [
                MockItem(f"Field on page {p}", _mock_bbox(0, 0, 100, 20), page_no=p)
                for p in range(1, page_count + 1)
            ]
            self.tables = []

    conv_pages = []
    for p in range(1, page_count + 1):
        conv_page = MagicMock()
        conv_page.page_no = p
        conv_page.size = MagicMock(width=595.0, height=842.0)
        conv_page.predictions.layout.clusters = []
        conv_pages.append(conv_page)

    mock_conv_result = MagicMock()
    mock_conv_result.document = MockDoc()
    mock_conv_result.pages = conv_pages
    return mock_conv_result


def test_docling_parser_captures_document_level_markdown(monkeypatch):
    """DoclingParseResult.document_markdown must carry the whole-document
    export_to_markdown() output verbatim (text + TableFormer tables, reading order) --
    distinct from the per-table markdown already captured on each TableStructure. Must
    also pass traverse_pictures=True (required whenever force_full_page_ocr was used --
    Docling's own docstring: without it, full-page-OCR text nested under a top-level
    PictureItem is silently skipped, yielding an empty/unstructured export)."""
    from idp.services.docling.parser import DoclingParser

    parser = DoclingParser()
    mock_conv_result = _build_mock_conv_result()
    export_calls = []

    def _export_to_markdown(**kwargs):
        export_calls.append(kwargs)
        return "# Loan Application Form\n\n| Field | Value |\n|---|---|\n| Name | Jane Doe |\n"

    mock_conv_result.document.export_to_markdown = _export_to_markdown

    mock_converter = MagicMock()
    mock_converter.convert.return_value = mock_conv_result
    monkeypatch.setattr(parser.pipeline, "get_converter", lambda: mock_converter)

    result = parser.parse("dummy.pdf", doc_id="TEST-DOC")

    assert export_calls == [{"traverse_pictures": True, "page_no": 1}]

    assert result.document_markdown is not None
    assert "# Loan Application Form" in result.document_markdown
    assert "| Name | Jane Doe |" in result.document_markdown


def test_docling_parser_document_markdown_covers_every_page(monkeypatch):
    """Regression: a single whole-document export_to_markdown() call walks doc.body's
    tree, which can silently drop a later page's content when it isn't cleanly linked
    into that tree (seen with multi-page full-page-OCR input) even though the page's
    items are present and correctly page-tagged in the flat doc.texts/doc.tables lists
    this parser already reads elements/tables from. Exporting per page via `pages={page_no}`
    filters by each item's own prov[].page_no instead of tree position, so every page must
    show up in document_markdown regardless of body-tree linkage."""
    from idp.services.docling.parser import DoclingParser

    parser = DoclingParser()
    mock_conv_result = _build_mock_conv_result(page_count=2)
    export_calls = []

    def _export_to_markdown(**kwargs):
        export_calls.append(kwargs)
        pno = kwargs.get("page_no")
        return f"## Page {pno} content\n\nSome text on page {pno}.\n"

    mock_conv_result.document.export_to_markdown = _export_to_markdown

    mock_converter = MagicMock()
    mock_converter.convert.return_value = mock_conv_result
    monkeypatch.setattr(parser.pipeline, "get_converter", lambda: mock_converter)

    result = parser.parse("dummy.pdf", doc_id="TEST-DOC")

    assert export_calls == [
        {"traverse_pictures": True, "page_no": 1},
        {"traverse_pictures": True, "page_no": 2},
    ]
    assert result.document_markdown is not None
    assert "Page 1 content" in result.document_markdown
    assert "Page 2 content" in result.document_markdown


def test_docling_parser_document_markdown_skips_blank_pages(monkeypatch):
    """A page whose export comes back empty (e.g. a genuinely blank page) must be
    dropped from the joined output rather than leaving a stray separator."""
    from idp.services.docling.parser import DoclingParser

    parser = DoclingParser()
    mock_conv_result = _build_mock_conv_result(page_count=2)

    def _export_to_markdown(**kwargs):
        return "Real content" if kwargs.get("page_no") == 1 else "   "

    mock_conv_result.document.export_to_markdown = _export_to_markdown

    mock_converter = MagicMock()
    mock_converter.convert.return_value = mock_conv_result
    monkeypatch.setattr(parser.pipeline, "get_converter", lambda: mock_converter)

    result = parser.parse("dummy.pdf", doc_id="TEST-DOC")

    assert result.document_markdown == "Real content"


def test_docling_parser_document_markdown_none_when_export_fails(monkeypatch):
    """A broken/absent export_to_markdown() must never fail parsing -- document_markdown
    degrades to None, exactly like the existing per-table export_to_markdown try/except."""
    from idp.services.docling.parser import DoclingParser

    parser = DoclingParser()
    mock_conv_result = _build_mock_conv_result()

    def _raise(**kwargs):
        raise RuntimeError("markdown export blew up")
    mock_conv_result.document.export_to_markdown = _raise

    mock_converter = MagicMock()
    mock_converter.convert.return_value = mock_conv_result
    monkeypatch.setattr(parser.pipeline, "get_converter", lambda: mock_converter)

    result = parser.parse("dummy.pdf", doc_id="TEST-DOC")

    assert result.document_markdown is None
    # The rest of parsing must still have succeeded despite the markdown failure.
    assert len(result.elements) == 1


def test_docling_parser_document_markdown_none_when_method_absent(monkeypatch):
    """An older Docling document object with no export_to_markdown attribute at all must
    not raise -- the hasattr guard should simply leave document_markdown as None."""
    from idp.services.docling.parser import DoclingParser

    parser = DoclingParser()
    mock_conv_result = _build_mock_conv_result()
    # MockDoc (a plain class, not a MagicMock) never defines export_to_markdown, so
    # hasattr(doc, "export_to_markdown") is already False here -- nothing to remove.
    assert not hasattr(mock_conv_result.document, "export_to_markdown")

    mock_converter = MagicMock()
    mock_converter.convert.return_value = mock_conv_result
    monkeypatch.setattr(parser.pipeline, "get_converter", lambda: mock_converter)

    result = parser.parse("dummy.pdf", doc_id="TEST-DOC")

    assert result.document_markdown is None


# === NEW TESTS FOR BOUNDING BOX FORMATION FIX ===

def test_degenerate_bbox_all_zero_is_flagged():
    """Previously skipped [0,0,0,0] boxes due to guard bug - now properly flagged."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Test the fixed _is_bbox_unformed method directly
    assert evaluator._is_bbox_unformed([0, 0, 0, 0]) is True
    assert evaluator._is_bbox_unformed([0.0, 0.0, 0.0, 0.0]) is True
    assert evaluator._is_bbox_unformed(None) is True
    assert evaluator._is_bbox_unformed([]) is True
    assert evaluator._is_bbox_unformed([10, 20]) is True  # Too few coords
    
    # Test penalty is applied in compute_text_confidence
    score_normal = evaluator.compute_text_confidence("Valid Text", bbox=[10, 10, 100, 50])
    score_zero = evaluator.compute_text_confidence("Valid Text", bbox=[0, 0, 0, 0])
    
    # Zero bbox should have 0.30 penalty applied
    assert score_zero < score_normal
    assert abs(score_normal - score_zero) >= 0.25  # Should see substantial penalty


def test_none_bbox_still_skippable_when_not_passed():
    """Preserve existing behavior for callers that don't provide bbox."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # These should have identical scores - no penalty when bbox=None
    score_no_bbox = evaluator.compute_text_confidence("Valid Text")
    score_none_bbox = evaluator.compute_text_confidence("Valid Text", bbox=None)
    
    assert score_no_bbox == score_none_bbox


def test_layout_passthrough_empty_text():
    """Elements with valid bbox but empty/generic text should be flagged."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Mock element with valid bbox but empty text
    element = MagicMock()
    element.text = ""
    element.bbox = [10, 10, 100, 50]
    element.metadata = {}
    element.label = "Text Block"
    element.source = "docling"
    
    assert evaluator.is_layout_passthrough(element) is True
    
    # Test with text that equals the generic label
    element.text = "Text Block"
    assert evaluator.is_layout_passthrough(element) is True
    
    # Test with other passthrough labels
    element.text = "Picture"
    assert evaluator.is_layout_passthrough(element) is True


def test_layout_passthrough_label_echo():
    """Text that echoes the layout label should be flagged as passthrough."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    element = MagicMock()
    element.bbox = [10, 10, 100, 50]
    element.metadata = {}
    element.source = "docling"
    
    # Test all passthrough labels
    for label in ["Text Block", "Picture", "Container", "Form Field"]:
        element.text = label
        element.label = label
        assert evaluator.is_layout_passthrough(element) is True, f"Failed for label: {label}"


def test_layout_passthrough_disproportionate_box():
    """Very wide boxes for short text should be flagged as layout containers."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    element = MagicMock()
    element.metadata = {}
    element.label = "text"
    element.source = "rapidocr"
    
    # Short text in very wide box (logo case)
    element.text = "HDB"  # 3 characters
    element.bbox = [10, 10, 200, 30]  # Width=190, Height=20, expected_width ≈ 3*20*0.55=33
    # Actual width (190) > expected_width*4 (33*4=132), should be flagged
    
    assert evaluator.is_layout_passthrough(element) is True
    
    # Same text in appropriately sized box should not be flagged
    element.bbox = [10, 10, 50, 30]  # Width=40, reasonable for 3 chars
    assert evaluator.is_layout_passthrough(element) is False


def test_real_ocr_token_not_flagged():
    """Valid OCR elements should not be flagged as passthrough."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    element = MagicMock()
    element.text = "Applicant Name"
    element.bbox = [10, 10, 120, 30]  # Reasonable size for text
    element.metadata = {"ocr_engine": "rapidocr"}
    element.label = "text"
    element.source = "rapidocr"
    
    assert evaluator.is_layout_passthrough(element) is False


def test_reconstruction_from_neighbors():
    """Passthrough elements should reconstruct bbox from valid sibling tokens."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Create passthrough element (wide container)
    passthrough = MagicMock()
    passthrough.text = "Text Block"
    passthrough.bbox = [0, 100, 200, 120]  # Wide container
    passthrough.metadata = {}
    passthrough.label = "Text Block"
    passthrough.source = "docling"
    
    # Create valid neighbor elements within the container
    neighbor1 = MagicMock()
    neighbor1.text = "Business"
    neighbor1.bbox = [10, 105, 50, 115]
    neighbor1.metadata = {}
    neighbor1.source = "rapidocr"
    
    neighbor2 = MagicMock()
    neighbor2.text = "Self employed"
    neighbor2.bbox = [60, 105, 120, 115]
    neighbor2.metadata = {}
    neighbor2.source = "rapidocr"
    
    neighbor3 = MagicMock()
    neighbor3.text = "Professional"
    neighbor3.bbox = [130, 105, 190, 115]
    neighbor3.metadata = {}
    neighbor3.source = "rapidocr"
    
    all_elements = [passthrough, neighbor1, neighbor2, neighbor3]
    
    # Reconstruct bbox
    reconstructed = evaluator.reconstruct_bbox_from_row_neighbors(
        passthrough, all_elements
    )
    
    # Should return union of neighbor boxes: [10, 105, 190, 115]
    assert reconstructed is not None
    assert reconstructed[0] == 10  # min x
    assert reconstructed[1] == 105  # min y
    assert reconstructed[2] == 190  # max x
    assert reconstructed[3] == 115  # max y
    
    # Check metadata was updated
    assert passthrough.metadata["bbox_source"] == "reconstructed_from_neighbors"
    assert passthrough.metadata["bbox_reconstruction_token_count"] == 3


def test_reconstruction_no_candidates_logo_case():
    """Passthrough with no valid neighbors should keep original bbox and flag as logo."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Logo element with no text neighbors
    logo = MagicMock()
    logo.text = "Picture"
    logo.bbox = [50, 50, 100, 80]
    logo.metadata = {}
    logo.label = "Picture"
    logo.source = "docling"
    
    # No neighbor elements
    all_elements = [logo]
    
    reconstructed = evaluator.reconstruct_bbox_from_row_neighbors(logo, all_elements)
    
    # Should return original bbox
    assert reconstructed == [50, 50, 100, 80]
    
    # Should be marked as fallback
    assert logo.metadata["bbox_source"] == "layout_fallback_no_text"
    assert logo.metadata["bbox_reconstruction_token_count"] == 0


def test_evaluate_element_sets_needs_vlm_and_flag():
    """Passthrough elements should be marked for VLM review with appropriate metadata."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Create passthrough element
    element = OCRElement(
        id="test",
        text="Text Block",
        bbox=[10, 10, 200, 30],
        confidence=0.95,  # High confidence but still passthrough
        page_number=1,
        metadata={}
    )
    element.label = "Text Block"
    element.source = "docling"
    
    # Evaluate the element
    evaluated = evaluator.evaluate_element(element)
    
    # Should be flagged for VLM despite high confidence
    assert evaluated.needs_vlm is True
    assert evaluated.metadata["layout_passthrough"] is True


def test_no_regression_on_clean_element():
    """Standard valid elements should be unaffected by the fix."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Clean, valid element
    element = OCRElement(
        id="clean",
        text="Applicant Name",
        bbox=[10, 10, 100, 25],
        confidence=0.95,
        page_number=1,
        metadata={"ocr_engine": "rapidocr"}
    )
    element.source = "rapidocr"
    
    # Evaluate element
    evaluated = evaluator.evaluate_element(element)
    
    # Should not be flagged as passthrough or needing VLM
    assert evaluated.needs_vlm is False
    assert "layout_passthrough" not in evaluated.metadata
    
    # Confidence should remain high
    assert evaluated.confidence >= 0.90


def test_bbox_malformed_edge_cases():
    """Test robustness against various malformed bbox inputs."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)
    
    # Test various malformed bbox cases
    malformed_cases = [
        None,
        [],
        [10],
        [10, 20],
        [10, 20, 30],
        ["a", "b", "c", "d"],  # Non-numeric
        [float('inf'), 0, 10, 10],  # Infinity
        [float('nan'), 0, 10, 10],  # NaN
    ]
    
    for bbox in malformed_cases:
        assert evaluator._is_bbox_unformed(bbox) is True, f"Should flag malformed bbox: {bbox}"

        # Should not crash in compute_text_confidence
        score = evaluator.compute_text_confidence("Valid Text", bbox=bbox)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


# === FORM-FIELD LOSS PREVENTION (loan application form) ===
# Regression coverage for whole rows / field values being dropped because the
# fused single-line OCR string tripped is_garbled_text() or was gutted by
# clean_bilingual_label_noise() before the serializer ever kept the element.


def test_form_abbreviations_not_flagged_as_garbled():
    """Printed loan-form labels / checkbox options must survive the garble gate."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)

    kept = [
        "Type of company Pvt. Ltd. Public Ltd. Ltd. Liability Co.",
        "PSU Govt. MNC Other",
        "Preferred mode of Communication : Whatsapp SMS E Mail",
        "CKYC No. UPIID/VPA",
        "STD",
        "HDB FINANCIAL SERVICES",
        "GSTIN available: Yes No",
    ]
    for row in kept:
        cleaned = OCRConfidenceEvaluator.clean_bilingual_label_noise(row)
        assert cleaned.strip(), f"row was gutted to empty: {row!r}"
        assert not evaluator.is_garbled_text(cleaned), f"falsely flagged garbled: {row!r} -> {cleaned!r}"


def test_true_devanagari_garble_still_flagged():
    """The fix must not blunt detection of real Devanagari->Latin OCR noise."""
    evaluator = OCRConfidenceEvaluator(threshold=0.70)

    for noise in ["3TET", "3RRTO xyz", "HRTRR", "RROR", "~~~~~", "!@#$%^&*"]:
        assert evaluator.is_garbled_text(noise) is True, f"should still be garbled: {noise!r}"


def test_clean_bilingual_preserves_alphanumeric_field_values():
    """Application no. / GSTIN / account no. / STD code are real values, not label garble."""
    clean = OCRConfidenceEvaluator.clean_bilingual_label_noise

    # Application number must not be stripped out of the row
    assert "APPL00343265" in clean("Application Date:06082026 ApplINo.:APPL00343265")
    # GSTIN value retained
    assert "08AOOPK6924P1ZZ" in clean("GSTIN available: Yes No GSTIN No. 08AOOPK6924P1ZZ")
    # Bundled STD code + number retained (was previously wiped to "")
    assert clean("STD0141").strip() == "STD0141"
    # Account number retained
    assert "50200064998229" in clean("Account Number 50200064998229")
    # Bank / PAN run retained
    assert "PANA00PK6924P" in clean("Mobile9079533555 PANA00PK6924P")


def test_clean_bilingual_still_strips_short_misread_blobs():
    """Short, letter-heavy Devanagari blobs are still removed (contract preserved)."""
    clean = OCRConfidenceEvaluator.clean_bilingual_label_noise

    assert clean("FarHToT 3RRTO INCOME TAX DEPARTMENT") == "INCOME TAX DEPARTMENT"
    assert "3TET" not in clean("Name 3TET RAMESH")
    assert "9HTET" not in clean("Address 9HTET LINE")


def test_clean_bilingual_keeps_middle_initial_and_split_option():
    """Lone uppercase letter flanked by words is a middle initial / split label, not noise."""
    clean = OCRConfidenceEvaluator.clean_bilingual_label_noise

    assert clean("RAJESH K SHARMA") == "RAJESH K SHARMA"
    # leading isolated noise letter is still removed
    assert clean("R BAZAR DIST JAIPUR").startswith("BAZAR")


def test_clean_bilingual_wipes_isolated_single_letter_by_design():
    """
    Pinning test, not a desired behavior: the lone-uppercase-letter guard above
    only looks at what flanks the letter WITHIN THE SAME STRING. A comb-box
    OCR element's text IS a single character by design (one printed cell per
    LayoutElement) -- its neighbors are separate elements this function never
    sees -- so the guard can never pass and the character is wiped to "".

    This is exactly why idp/services/output/serializer.py routes comb-box
    candidate elements (CombBoxDetector.is_candidate_text) around this
    function entirely instead of calling it (see the "PRODUCTION FIX" comment
    at the `final_text = ...` line there) -- fixing it here would defeat the
    flanking-context guard this function relies on for its real job (label
    lines like "RAJESH K SHARMA" above). Regression coverage for the actual
    fix lives in tests/idp/unit/test_serializer.py
    (test_comb_box_merged_tokens_stay_near_their_field_label), which proves
    single-letter comb-box cells like "P"/"A"/"I" survive end-to-end and
    reconstruct into full field values.
    """
    clean = OCRConfidenceEvaluator.clean_bilingual_label_noise

    for letter in ["P", "A", "I", "K"]:
        assert clean(letter) == "", (
            f"clean_bilingual_label_noise({letter!r}) no longer wipes an "
            "isolated letter -- if this changed intentionally, confirm "
            "serializer.py's comb-candidate bypass is still necessary/correct."
        )
