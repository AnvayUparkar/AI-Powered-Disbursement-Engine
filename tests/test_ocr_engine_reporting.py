"""Tests for reporting which OCR engine produced a document's text (LightOnOCR via LiteLLM vs Docling).

Covers the idp serializer's ProcessingMetadata.ocr_engine, the registry helpers that turn it into the
UI's ocrEngine field / "LightOnOCR" processing step, and the case-document path via idp_scan's
"_processing" key.
"""
from unittest.mock import patch

import pytest

from app.services.registry.normalizer import (
    build_lightonocr_processing_step,
    build_ocr_engine_info,
    build_processing_steps,
    normalize_uploaded_record,
)
from idp.core.config import settings
from idp.models.ocr import OCRElement, OCRResult
from idp.models.processing import ProcessingMetrics
from idp.services.ocr.lightonocr_engine import LIGHTONOCR_ENGINE_ID
from idp.services.output.serializer import DocumentSerializer


def _lightonocr_processing(processed=3, failed=0, seconds=12.34):
    return {
        "ocr_engine": LIGHTONOCR_ENGINE_ID,
        "ocr_model": "lightonai/LightOnOCR-2-1B",
        "vlm_used": False,
        "metrics": {
            "lightonocr_pages_processed": processed,
            "lightonocr_pages_failed": failed,
            "lightonocr_processing_time": seconds,
        },
    }


# ---------------------------------------------------------------- serializer (idp pod)

def _build(metrics, ocr_results):
    return DocumentSerializer().build_unified_document(
        doc_id="DOC-T", filename="scan.pdf", mime_type="application/pdf", file_size_bytes=1024,
        page_count=1, docling_result=None, ocr_results=ocr_results, vlm_corrections={},
        metrics=metrics, docling_used=False,
    )


def test_serializer_reports_lightonocr_engine_when_pages_went_through_it():
    metrics = ProcessingMetrics(lightonocr_pages_processed=1, lightonocr_processing_time=4.2)
    page = OCRResult(page_number=1, image_width=595.0, image_height=842.0, elements=[
        OCRElement(id="lightonocr-p1-full", text="LOAN APPLICATION FORM", bbox=[0, 0, 595, 842],
                   confidence=0.95, page_number=1, source="lightonocr"),
    ])
    with patch.object(settings, "LIGHTONOCR_MODEL", "lightonai/LightOnOCR-2-1B"):
        doc = _build(metrics, [page])
    assert doc.processing.ocr_engine == LIGHTONOCR_ENGINE_ID
    assert doc.processing.ocr_model == "lightonai/LightOnOCR-2-1B"
    assert doc.processing.metrics.lightonocr_pages_processed == 1


def test_serializer_reports_lightonocr_even_when_every_page_failed():
    """Edge case: the route was taken (Docling never ran) even though no page succeeded."""
    metrics = ProcessingMetrics(lightonocr_pages_failed=2)
    failed = OCRResult(page_number=1, elements=[], extraction_failed=True, image_width=595.0, image_height=842.0)
    doc = _build(metrics, [failed])
    assert doc.processing.ocr_engine == LIGHTONOCR_ENGINE_ID


def test_serializer_keeps_docling_engine_when_lightonocr_not_used():
    doc = _build(ProcessingMetrics(), [])
    assert doc.processing.ocr_engine == "docling_rapidocr"
    assert doc.processing.ocr_model == "PP-OCRv6_MEDIUM"


# ---------------------------------------------------------------- registry helpers (api pod)

def test_engine_info_for_lightonocr():
    info = build_ocr_engine_info(_lightonocr_processing(processed=2, failed=1, seconds=9.5))
    assert info == {
        "engine": "lightonocr",
        "label": "LightOnOCR via LiteLLM",
        "model": "lightonai/LightOnOCR-2-1B",
        "viaLiteLLM": True,
        "pagesProcessed": 2,
        "pagesFailed": 1,
        "seconds": 9.5,
    }


def test_engine_info_for_docling():
    info = build_ocr_engine_info({"ocr_engine": "docling_rapidocr", "ocr_model": "PP-OCRv6_MEDIUM"})
    assert info["engine"] == "docling" and info["viaLiteLLM"] is False


@pytest.mark.parametrize("processing", [None, {}, {"ocr_engine": "none"}, "not-a-dict"])
def test_engine_info_unknown_returns_none(processing):
    """XML / signature fast paths and older results carry no usable engine record."""
    assert build_ocr_engine_info(processing) is None


def test_engine_info_missing_metrics_defaults_to_zero_pages():
    info = build_ocr_engine_info({"ocr_engine": LIGHTONOCR_ENGINE_ID, "ocr_model": "m"})
    assert info["pagesProcessed"] == 0 and info["pagesFailed"] == 0 and info["seconds"] is None


@pytest.mark.parametrize("processed, failed, status", [(3, 0, "COMPLETED"), (2, 1, "WARNING"), (0, 2, "FAILED")])
def test_lightonocr_step_status(processed, failed, status):
    step = build_lightonocr_processing_step("DOC-1", build_ocr_engine_info(_lightonocr_processing(processed, failed)))
    assert step["component"] == "LightOnOCR"
    assert step["status"] == status
    assert f"via LiteLLM read {processed}/{processed + failed} scanned page(s)" in step["detail"]
    assert ("failed" in step["detail"]) is (failed > 0)


def test_processing_steps_switch_on_engine():
    lo = build_processing_steps("DOC-1", build_ocr_engine_info(_lightonocr_processing()))
    assert [s["component"] for s in lo] == ["LightOnOCR"]
    default = build_processing_steps("DOC-1", None)
    assert [s["component"] for s in default] == ["Docling", "PaddleOCR"]


def test_normalized_record_exposes_ocr_engine_and_step():
    rec = normalize_uploaded_record(
        doc_id="DOC-9", filename="scan.pdf", detected_type="Application Form", assoc_case="GENERAL",
        parsed_result={"text": "hello", "processing": _lightonocr_processing()},
    )
    assert rec["ocrEngine"]["engine"] == "lightonocr"
    assert rec["processingSteps"][0]["component"] == "LightOnOCR"


def test_normalized_record_without_parse_has_no_engine():
    rec = normalize_uploaded_record(doc_id="DOC-10", filename="a.pdf", detected_type="PAN", assoc_case="GENERAL")
    assert rec["ocrEngine"] is None
    assert [s["component"] for s in rec["processingSteps"]] == ["Docling", "PaddleOCR"]


# ---------------------------------------------------------------- case-document path (idp_scan -> case_scanner)

def test_idp_scan_result_carries_processing_for_case_scanner():
    from idp.models.document import DocumentSource, ParsedDocument
    from idp.models.processing import ProcessingMetadata
    from pipeline.nodes import idp_scan

    parsed = ParsedDocument(
        document_id="LOAN_1_application_form",
        source=DocumentSource(filename="application_form.pdf", mime_type="application/pdf"),
        text="APPLICANT NAME RAVI KUMAR",
        processing=ProcessingMetadata(
            document_id="LOAN_1_application_form", processing_id="p", file_type="pdf",
            mime_type="application/pdf", file_size_bytes=10, page_count=2, docling_used=False,
            ocr_engine=LIGHTONOCR_ENGINE_ID, ocr_model="lightonai/LightOnOCR-2-1B",
            metrics=ProcessingMetrics(lightonocr_pages_processed=2, lightonocr_processing_time=7.0),
        ),
        custom_metadata={"llm_extracted_fields": {"applicant_name": "RAVI KUMAR"}},
    )
    with patch.object(idp_scan, "llm_extract_fields", side_effect=AssertionError("LLM must not be called")):
        result = idp_scan.build_idp_result_from_parsed(parsed, doc_type="application_form", doc_id="LOAN_1_application_form")

    info = build_ocr_engine_info(result["_processing"])
    assert info["engine"] == "lightonocr"
    assert info["pagesProcessed"] == 2 and info["seconds"] == 7.0
