"""Real, model-reported confidences must reach DoclingParseResult unaltered.

Each stage is surfaced separately so it can be debugged on its own:
  * ocr_confidence     -- RapidOCR's per-text-cell recognition score
  * layout_confidence  -- the Docling layout model's per-cluster score
  * table_confidence   -- the score of the region TableFormer built a grid from
  * layout/ocr/table/parse_score -- Docling's own document-level quality report

Docling is stubbed so the assertions are exact and no model is loaded.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from idp.services.docling.parser import DoclingParser, _finite, _grade_str, _overlap_ratio


class _Box:
    """Stand-in for a Docling BoundingBox already in top-left origin."""
    def __init__(self, l, t, r, b):
        self.l, self.t, self.r, self.b = l, t, r, b


def _cluster(bbox, layout_conf, cell_confs):
    return SimpleNamespace(
        bbox=_Box(*bbox),
        confidence=layout_conf,
        cells=[SimpleNamespace(confidence=c) for c in cell_confs],
    )


def _parse_with(monkeypatch, texts, clusters, confidence=None):
    parser = DoclingParser()
    page = SimpleNamespace(
        page_no=0,
        size=SimpleNamespace(width=600.0, height=800.0),
        predictions=SimpleNamespace(layout=SimpleNamespace(clusters=clusters)),
    )
    doc = SimpleNamespace(pages={}, texts=texts, tables=[])
    conv = MagicMock()
    conv.document = doc
    conv.pages = [page]
    conv.confidence = confidence
    converter = MagicMock()
    converter.convert.return_value = conv
    monkeypatch.setattr(parser.pipeline, "get_converter", lambda: converter)
    return parser.parse("dummy.pdf", doc_id="T")


def _text_item(text, bbox):
    return SimpleNamespace(
        text=text, label="text",
        prov=[SimpleNamespace(page_no=1, bbox=_Box(*bbox))],
    )


def test_element_carries_both_model_scores(monkeypatch):
    """Happy path: the overlapping cluster's scores land on the element verbatim."""
    res = _parse_with(
        monkeypatch,
        texts=[_text_item("HAGP1388", (100, 100, 200, 140))],
        clusters=[_cluster((90, 90, 210, 150), layout_conf=0.9073, cell_confs=[0.9267])],
    )
    elem = res.elements[0]
    assert elem.ocr_confidence == pytest.approx(0.9267)
    assert elem.layout_confidence == pytest.approx(0.9073)
    # `confidence` mirrors the OCR score for existing consumers (VLM router, comb-box).
    assert elem.confidence == pytest.approx(0.9267)


def test_multiple_cells_average_into_one_ocr_score(monkeypatch):
    """A cluster Docling merged from several cells reports their mean recognition score."""
    res = _parse_with(
        monkeypatch,
        texts=[_text_item("Notes No", (0, 0, 100, 50))],
        clusters=[_cluster((0, 0, 100, 50), layout_conf=0.55, cell_confs=[1.0, 0.98])],
    )
    assert res.elements[0].ocr_confidence == pytest.approx(0.99)
    assert res.elements[0].layout_confidence == pytest.approx(0.55)


def test_non_overlapping_cluster_is_not_borrowed(monkeypatch):
    """Edge: an element with no overlapping cluster reports unknown, not a neighbour's score."""
    res = _parse_with(
        monkeypatch,
        texts=[_text_item("Anvay", (500, 700, 560, 760))],
        clusters=[_cluster((0, 0, 50, 50), layout_conf=0.99, cell_confs=[0.99])],
    )
    assert res.elements[0].ocr_confidence is None
    assert res.elements[0].layout_confidence is None


def test_document_level_stage_scores_are_surfaced(monkeypatch):
    """The four per-stage scores and the grade reach the caller for side-by-side debugging."""
    report = SimpleNamespace(
        layout_score=0.9367420, ocr_score=0.9709707,
        table_score=float("nan"), parse_score=float("nan"),
        mean_grade=SimpleNamespace(value="excellent"),
    )
    res = _parse_with(monkeypatch, texts=[], clusters=[], confidence=report)
    assert res.layout_score == pytest.approx(0.9367)
    assert res.ocr_score == pytest.approx(0.971)
    # NaN means "stage did not run" and must not be reported as a number.
    assert res.table_score is None
    assert res.parse_score is None
    assert res.quality_grade == "excellent"


@pytest.mark.parametrize(
    "value,expected",
    [(0.5, 0.5), (float("nan"), None), (float("inf"), None), (None, None), ("x", None)],
)
def test_finite_rejects_non_numbers_and_nan(value, expected):
    assert _finite(value) == expected


@pytest.mark.parametrize(
    "grade,expected",
    [
        (SimpleNamespace(value="poor"), "poor"),
        ("fair", "fair"),
        (None, None),
        (MagicMock(), None),  # a non-string .value must not break result validation
    ],
)
def test_grade_coercion_never_yields_a_non_string(grade, expected):
    """Regression: a non-string grade previously failed validation and silently forced
    the whole document onto the fallback parser."""
    assert _grade_str(grade) == expected


def test_overlap_ratio_is_relative_to_the_inner_box():
    assert _overlap_ratio([0, 0, 10, 10], [0, 0, 20, 20]) == pytest.approx(1.0)
    assert _overlap_ratio([0, 0, 10, 10], [5, 0, 20, 10]) == pytest.approx(0.5)
    assert _overlap_ratio([0, 0, 10, 10], [50, 50, 60, 60]) == 0.0
    assert _overlap_ratio([], [0, 0, 1, 1]) == 0.0


def test_cluster_page_numbers_are_not_offset(monkeypatch):
    """Regression: Docling builds pages as Page(page_no=i + 1), so page_no is already
    1-based. Adding an offset put every cluster on the wrong page key and silently
    returned None for every element's confidence."""
    parser = DoclingParser()
    pages = [
        SimpleNamespace(
            page_no=n,
            size=SimpleNamespace(width=600.0, height=800.0),
            predictions=SimpleNamespace(layout=SimpleNamespace(
                clusters=[_cluster((0, 0, 100, 100), layout_conf=0.10 * n, cell_confs=[0.20 * n])]
            )),
        )
        for n in (1, 2)
    ]
    texts = [
        SimpleNamespace(text=f"page {n}", label="text",
                        prov=[SimpleNamespace(page_no=n, bbox=_Box(10, 10, 90, 90))])
        for n in (1, 2)
    ]
    conv = MagicMock()
    conv.document = SimpleNamespace(pages={}, texts=texts, tables=[])
    conv.pages = pages
    conv.confidence = None
    converter = MagicMock()
    converter.convert.return_value = conv
    monkeypatch.setattr(parser.pipeline, "get_converter", lambda: converter)

    res = parser.parse("dummy.pdf", doc_id="T")
    by_page = {e.page_number: e for e in res.elements}
    assert by_page[1].layout_confidence == pytest.approx(0.10)
    assert by_page[2].layout_confidence == pytest.approx(0.20)
    assert by_page[1].ocr_confidence == pytest.approx(0.20)
    assert by_page[2].ocr_confidence == pytest.approx(0.40)


def test_serializer_preserves_per_element_model_scores():
    """The serializer rebuilds every LayoutElement; the real scores must survive that.

    Regression: without explicit propagation the rebuilt elements dropped ocr_confidence and
    layout_confidence, so the document-level scores looked correct while every box in the
    debug overlay fell back to "unknown".
    """
    from idp.models.layout import ElementType, LayoutElement
    from idp.models.processing import ProcessingMetrics
    from idp.services.docling.parser import DoclingParseResult
    from idp.services.output.serializer import DocumentSerializer

    parse_result = DoclingParseResult(
        elements=[
            LayoutElement(
                id="e1", type=ElementType.PARAGRAPH, text="Harshit Mishra",
                bbox=[100.0, 100.0, 300.0, 140.0], page_number=1,
                confidence=0.948, ocr_confidence=0.948, layout_confidence=0.5549,
                source="docling_ocr", structure_source="docling",
            )
        ],
        tables=[], page_count=1,
        pages_dimensions=[{"width": 600.0, "height": 800.0}],
        layout_score=0.9367, ocr_score=0.971, table_score=None,
        parse_score=None, quality_grade="excellent",
    )

    doc = DocumentSerializer().build_unified_document(
        doc_id="T", filename="f.jpeg", mime_type="image/jpeg", file_size_bytes=1,
        page_count=1, docling_result=parse_result, ocr_results=[], vlm_corrections={},
        metrics=ProcessingMetrics(), s3_bucket="b", s3_key="k",
        docling_used=True, vlm_used=False,
    )

    assert len(doc.elements) == 1
    elem = doc.elements[0]
    assert elem.ocr_confidence == pytest.approx(0.948)
    assert elem.layout_confidence == pytest.approx(0.5549)
    # Document-level scores ride through to the same object the API returns.
    assert doc.layout_score == pytest.approx(0.9367)
    assert doc.ocr_score == pytest.approx(0.971)
    assert doc.table_score is None
    assert doc.quality_grade == "excellent"


def test_field_location_reports_both_model_scores():
    """Every resolved field must carry the OCR and layout scores of the token it matched,
    so the provenance panel can show which stage was uncertain about that value."""
    from idp.services.extraction.field_location_resolver import FieldLocationResolver

    elements = [{
        "id": "e1", "text": "ABCDE1234F", "bbox": [0.1, 0.1, 0.4, 0.2],
        "page_number": 1, "confidence": 0.93,
        "ocr_confidence": 0.93, "layout_confidence": 0.55, "source": "docling_ocr",
    }]
    locs = FieldLocationResolver().resolve_field_locations(
        extracted_fields={"pan_number": "ABCDE1234F"},
        ocr_elements=elements,
        page_dimensions=[{"width": 600.0, "height": 800.0}],
    )
    loc = locs["pan_number"]
    assert loc.location_status == "resolved"
    assert loc.ocr_confidence == pytest.approx(0.93)
    assert loc.layout_confidence == pytest.approx(0.55)


def test_unresolved_field_reports_no_scores():
    """Edge: a value the resolver cannot find must report unknown, never a borrowed score."""
    from idp.services.extraction.field_location_resolver import FieldLocationResolver

    locs = FieldLocationResolver().resolve_field_locations(
        extracted_fields={"pan_number": "ZZZZZ9999Z"},
        ocr_elements=[{
            "id": "e1", "text": "something else", "bbox": [0.1, 0.1, 0.4, 0.2],
            "page_number": 1, "confidence": 0.9,
            "ocr_confidence": 0.9, "layout_confidence": 0.9,
        }],
        page_dimensions=[{"width": 600.0, "height": 800.0}],
    )
    loc = locs["pan_number"]
    assert loc.location_status == "unresolved"
    assert loc.layout_confidence is None
    assert loc.bbox is None


def test_key_value_extractor_paragraphs_keep_model_scores():
    """Paragraph records feed the provenance panel by a different route than field_locations.

    Regression: they carried only the legacy `confidence`, so the panel showed a real
    percentage in the bar while both model chips read "n/a".
    """
    from pipeline.engines.key_value_extractor import KeyValueExtractor

    elements = [{
        "id": "e1", "text": "Aadhaas Name", "bbox": [0.4, 0.3, 0.6, 0.4],
        "page_number": 1, "type": "text", "confidence": 0.9173,
        "ocr_confidence": 0.9173, "layout_confidence": 0.5549, "source": "docling_ocr",
    }]
    out = KeyValueExtractor().extract(elements, doc_type="application_form")

    paragraphs = out.get("paragraphs") or []
    assert paragraphs, "expected the unmatched element to surface as a paragraph"
    para = paragraphs[0]
    assert para["ocr_confidence"] == pytest.approx(0.9173)
    assert para["layout_confidence"] == pytest.approx(0.5549)
