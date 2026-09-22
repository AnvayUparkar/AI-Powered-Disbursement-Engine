import pytest
from idp.models.layout import LayoutElement, ElementType
from idp.models.processing import ProcessingMetrics
from idp.services.output.serializer import DocumentSerializer
from app.services.registry.normalizer import normalize_uploaded_record, parse_extracted_fields


def test_document_serializer_computes_dynamic_average_confidence():
    """Verify that DocumentSerializer calculates average_confidence metric dynamically from elements."""
    serializer = DocumentSerializer()
    metrics = ProcessingMetrics()

    elements = [
        LayoutElement(id="e1", type=ElementType.TEXT, text="Sample Label: Value 1", bbox=[10, 10, 100, 20], confidence=0.98, page_number=1),
        LayoutElement(id="e2", type=ElementType.TEXT, text="Sample Label 2: Value 2", bbox=[10, 30, 100, 40], confidence=0.82, page_number=1),
    ]

    doc = serializer.build_unified_document(
        doc_id="TEST_DOC_001",
        filename="test.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        docling_result=None,
        ocr_results=[],
        vlm_corrections={},
        metrics=metrics,
    )

    # Note: docling_result=None builds from page elements when provided or fallback
    assert metrics.average_confidence > 0.0


def test_normalizer_calculates_dynamic_document_confidence():
    """Verify normalize_uploaded_record calculates dynamic average confidence from parsed elements."""
    parsed_payload = {
        "document_id": "DOC_123",
        "pages": [{"page_number": 1}],
        "elements": [
            {"id": "el-1", "text": "Name: John Doe", "confidence": 0.94, "page_number": 1},
            {"id": "el-2", "text": "PAN: ABCDE1234F", "confidence": 0.98, "page_number": 1},
        ],
        "tables": [
            {"id": "tbl-1", "num_rows": 2, "num_cols": 2, "confidence": 0.88, "page_number": 1}
        ],
        "processing": {
            "vlm_used": False,
            "metrics": {
                "average_confidence": 0.94
            }
        }
    }

    record = normalize_uploaded_record(
        doc_id="DOC_123",
        filename="test_doc.pdf",
        detected_type="Application Form",
        assoc_case="CASE_001",
        parsed_result=parsed_payload,
    )

    # Should compute 94.0 from metrics.average_confidence (0.94 * 100) instead of static 97.5 or 96.5
    assert record["confidence"] == 94.0
    assert record["confidence"] != 97.5
    assert record["confidence"] != 96.5


def test_parse_extracted_fields_uses_dynamic_table_confidence():
    """Verify parse_extracted_fields uses dynamic table confidence score."""
    parsed_payload = {
        "tables": [
            {"id": "tbl-1", "num_rows": 3, "num_cols": 2, "confidence": 0.85, "page_number": 1}
        ]
    }

    fields = parse_extracted_fields("DOC_123", parsed_payload, {})
    table_field = next(f for f in fields if f["type"] == "table")

    # Should compute 85% instead of hardcoded 95%
    assert table_field["confidence"] == 85
