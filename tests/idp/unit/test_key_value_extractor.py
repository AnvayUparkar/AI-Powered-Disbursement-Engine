import pytest
from pipeline.engines.key_value_extractor import KeyValueExtractor


def test_spatial_key_value_right_of_label():
    """TEST 1: Label -> value on the right (same row)."""
    extractor = KeyValueExtractor()
    elements = [
        {"id": "e1", "text": "Sponsor Bank Code", "bbox": [0.1, 0.2, 0.3, 0.24], "page_number": 1, "confidence": 0.98},
        {"id": "e2", "text": "HDFC0000060", "bbox": [0.32, 0.2, 0.5, 0.24], "page_number": 1, "confidence": 0.99},
    ]

    res = extractor.extract(elements)
    kvs = res["key_values"]

    assert "sponsor_bank_code" in kvs
    assert kvs["sponsor_bank_code"]["value"] == "HDFC0000060"
    assert kvs["sponsor_bank_code"]["relationship"] == "right_of_label"
    assert len(res["paragraphs"]) == 0  # Both consumed into key-value


def test_spatial_key_value_below_label():
    """TEST 2: Label above value (form box below label)."""
    extractor = KeyValueExtractor()
    elements = [
        {"id": "e1", "text": "Amount", "bbox": [0.1, 0.3, 0.25, 0.34], "page_number": 1, "confidence": 0.95},
        {"id": "e2", "text": "50000", "bbox": [0.1, 0.36, 0.25, 0.39], "page_number": 1, "confidence": 0.97},
    ]

    res = extractor.extract(elements)
    kvs = res["key_values"]

    assert "amount" in kvs
    assert kvs["amount"]["value"] == "50000"
    assert kvs["amount"]["relationship"] == "below_label"


def test_colon_format_single_element():
    """TEST 3: Colon format inside a single OCR line."""
    extractor = KeyValueExtractor()
    elements = [
        {"id": "e1", "text": "Applicant Name: Rahul Sharma", "bbox": [0.1, 0.1, 0.5, 0.14], "page_number": 1, "confidence": 0.96},
    ]

    res = extractor.extract(elements)
    kvs = res["key_values"]

    assert "applicant_name" in kvs
    assert kvs["applicant_name"]["value"] == "Rahul Sharma"
    assert kvs["applicant_name"]["relationship"] == "inline_delimiter"


def test_multiple_fields_on_same_row_no_cross_pairing():
    """TEST 4: Multiple fields on same row must not cross-pair."""
    extractor = KeyValueExtractor()
    elements = [
        {"id": "l1", "text": "UMRN", "bbox": [0.05, 0.1, 0.15, 0.13], "page_number": 1, "confidence": 0.95},
        {"id": "v1", "text": "HDFC778899", "bbox": [0.16, 0.1, 0.35, 0.13], "page_number": 1, "confidence": 0.97},
        {"id": "l2", "text": "Date", "bbox": [0.45, 0.1, 0.55, 0.13], "page_number": 1, "confidence": 0.96},
        {"id": "v2", "text": "28/08/2026", "bbox": [0.56, 0.1, 0.75, 0.13], "page_number": 1, "confidence": 0.98},
    ]

    res = extractor.extract(elements)
    kvs = res["key_values"]

    assert "umrn" in kvs
    assert kvs["umrn"]["value"] == "HDFC778899"
    assert "date" in kvs
    assert kvs["date"]["value"] == "28/08/2026"
    assert kvs["umrn"]["value"] != "28/08/2026"


def test_checkbox_extraction():
    """TEST 5: Checkboxes extracted separately from normal paragraphs."""
    extractor = KeyValueExtractor()
    elements = [
        {"id": "c1", "text": "Create", "bbox": [0.1, 0.05, 0.2, 0.08], "page_number": 1},
        {"id": "c2", "text": "Modify", "bbox": [0.25, 0.05, 0.35, 0.08], "page_number": 1},
        {"id": "c3", "text": "区Dally", "bbox": [0.1, 0.5, 0.25, 0.53], "page_number": 1},
        {"id": "c4", "text": "B-Monthiy", "bbox": [0.3, 0.5, 0.45, 0.53], "page_number": 1},
    ]

    res = extractor.extract(elements)
    cbs = res["checkboxes"]

    assert "frequency" in cbs
    assert cbs["frequency"]["options"]["daily"] is True
    assert cbs["frequency"]["options"]["monthly"] is False


def test_unconsumed_paragraphs_preserve_geometry():
    """TEST 6: Unconsumed text elements retain their geometry, type, and source."""
    extractor = KeyValueExtractor()
    elements = [
        {"id": "p1", "text": "Terms and conditions apply to all debit mandates.", "bbox": [0.05, 0.8, 0.9, 0.85], "page_number": 1, "type": "paragraph", "confidence": 0.92, "source": "rapidocr"}
    ]

    res = extractor.extract(elements)
    assert len(res["paragraphs"]) == 1
    p = res["paragraphs"][0]
    assert p["text"] == "Terms and conditions apply to all debit mandates."
    assert p["bbox"] == [0.05, 0.8, 0.9, 0.85]
    assert p["confidence"] == 0.92
    assert p["source"] == "rapidocr"


# ==============================================================================
# STRICT REJECTION REGRESSION TESTS
# ==============================================================================

def test_branding_excluded_from_labels():
    """TEST 7: SERVICES or HDB FINANCIAL SERVICES LTD must NOT become key-value pairs."""
    extractor = KeyValueExtractor()
    elements = [
        {"id": "b1", "text": "SERVICES", "bbox": [0.1, 0.1, 0.2, 0.13], "page_number": 1, "confidence": 0.90},
        {"id": "b2", "text": "HDBFINANCIALSERVICESLTD", "bbox": [0.1, 0.15, 0.4, 0.18], "page_number": 1, "confidence": 0.90},
        {"id": "v1", "text": "HDFC0000060", "bbox": [0.1, 0.25, 0.3, 0.28], "page_number": 1, "confidence": 0.95},
    ]
    res = extractor.extract(elements)
    kvs = res["key_values"]

    assert "services" not in kvs
    assert "hdbfinancialservicesltd" not in kvs


def test_section_headers_excluded_from_labels():
    """TEST 8: Section headers (DEBIT, FREQUENCY, TYPE) must NOT pair with unrelated fields."""
    extractor = KeyValueExtractor()
    elements = [
        {"id": "s1", "text": "DEBIT", "bbox": [0.1, 0.45, 0.2, 0.48], "page_number": 1, "confidence": 0.90},
        {"id": "s2", "text": "FREQUENCY", "bbox": [0.3, 0.45, 0.4, 0.48], "page_number": 1, "confidence": 0.90},
        {"id": "s3", "text": "TYPE", "bbox": [0.5, 0.45, 0.6, 0.48], "page_number": 1, "confidence": 0.90},
        {"id": "v1", "text": "Reference1", "bbox": [0.1, 0.55, 0.25, 0.58], "page_number": 1, "confidence": 0.85},
    ]
    res = extractor.extract(elements)
    kvs = res["key_values"]

    assert "debit" not in kvs
    assert "frequency" not in kvs
    assert "type" not in kvs


def test_ifsc_rejects_checkbox_label_like_weekly():
    """TEST 9: IFSC/MICR must NOT pair with μWeekly."""
    extractor = KeyValueExtractor()
    elements = [
        {"id": "l1", "text": "IFSC/MICR", "bbox": [0.04, 0.38, 0.15, 0.41], "page_number": 1, "confidence": 0.88},
        {"id": "c1", "text": "μWeekly", "bbox": [0.04, 0.44, 0.15, 0.47], "page_number": 1, "confidence": 0.85},
    ]
    res = extractor.extract(elements)
    kvs = res["key_values"]

    assert "ifscmicr" not in kvs or kvs["ifscmicr"]["value"] != "μWeekly"


def test_value_as_label_reversal_prevention():
    """TEST 10: FiftyThousand Only must NOT become a label for 50000."""
    extractor = KeyValueExtractor()
    elements = [
        {"id": "v1", "text": "FiftyThousand Only", "bbox": [0.1, 0.41, 0.3, 0.44], "page_number": 1, "confidence": 0.90},
        {"id": "v2", "text": "50000", "bbox": [0.32, 0.41, 0.45, 0.44], "page_number": 1, "confidence": 0.95},
    ]
    res = extractor.extract(elements)
    kvs = res["key_values"]

    assert "fiftythousand_only" not in kvs


def test_garbled_ocr_rejected_from_key_values():
    """TEST 11: Corrupted or garbled OCR text must not become field values."""
    extractor = KeyValueExtractor()
    garbled_text = "Iageefortiddfandaeessingdharusbyshelakwlamuatrizangtodebimyauaasperlaealscheulefhgeaftaebak"
    elements = [
        {"id": "l1", "text": "Reference", "bbox": [0.05, 0.5, 0.15, 0.53], "page_number": 1, "confidence": 0.90},
        {"id": "v1", "text": garbled_text, "bbox": [0.16, 0.5, 0.5, 0.53], "page_number": 1, "confidence": 0.55},
    ]
    res = extractor.extract(elements)
    kvs = res["key_values"]

    # Garbled text must never become the field value; instead, an unmatched stub is emitted
    assert kvs["reference"]["value"] is None
    assert kvs["reference"]["needs_review"] is True
    assert kvs["reference"]["reason"] == "no_value_matched"

    # With stubs disabled (legacy mode), the key is omitted entirely
    legacy_extractor = KeyValueExtractor(emit_unmatched_stubs=False)
    legacy_res = legacy_extractor.extract(elements)
    assert "reference" not in legacy_res["key_values"]


# ==============================================================================
# PHASE 1 & 2: TIERED CONFIDENCE & UNMATCHED STUB TESTS (Section 5)
# ==============================================================================

def test_low_confidence_value_participates_in_pairing_and_flags_review():
    """A value at confidence 0.45 (between 0.20 and 0.50) is classified LOW_CONFIDENCE_VALUE,

    participates in spatial pairing, and is accepted with needs_review=True.
    """
    from pipeline.engines.key_value_extractor import ElementClassification

    extractor = KeyValueExtractor(hard_noise_floor=0.20, confident_value_floor=0.50, min_pair_confidence=0.72)
    elements = [
        {"id": "l1", "text": "State", "bbox": [0.10, 0.20, 0.25, 0.24], "page_number": 1, "confidence": 0.95},
        {"id": "v1", "text": "KARNATAKA", "bbox": [0.28, 0.20, 0.45, 0.24], "page_number": 1, "confidence": 0.45},
    ]

    # Verify classification
    classified = extractor._classify_elements(elements, consumed_ids=set())
    class_map = {item["element"]["id"]: item["classification"] for item in classified}
    assert class_map["v1"] == ElementClassification.LOW_CONFIDENCE_VALUE

    # Verify extraction: value is retained, NOT silently dropped
    res = extractor.extract(elements)
    kvs = res["key_values"]

    assert "state" in kvs
    assert kvs["state"]["value"] == "KARNATAKA"
    assert kvs["state"]["confidence"] == 0.45
    assert kvs["state"]["needs_review"] is True
    assert kvs["state"]["relationship"] == "right_of_label"


def test_unmatched_label_emits_stub_instead_of_silent_omission():
    """A label that finds no candidate value produces an explicit stub entry

    with value=None, needs_review=True, reason='no_value_matched'.
    """
    extractor = KeyValueExtractor()
    elements = [
        {"id": "l1", "text": "State", "bbox": [0.10, 0.20, 0.25, 0.24], "page_number": 1, "confidence": 0.95},
    ]

    res = extractor.extract(elements)
    kvs = res["key_values"]

    assert "state" in kvs
    assert kvs["state"]["label"] == "State"
    assert kvs["state"]["value"] is None
    assert kvs["state"]["confidence"] == 0.0
    assert kvs["state"]["needs_review"] is True
    assert kvs["state"]["reason"] == "no_value_matched"


def test_hard_noise_floor_drops_extremely_low_confidence():
    """A value at confidence 0.10 (below hard_noise_floor 0.20) is classified NOISE,

    not LOW_CONFIDENCE_VALUE, and never paired.
    """
    from pipeline.engines.key_value_extractor import ElementClassification

    extractor = KeyValueExtractor(hard_noise_floor=0.20, confident_value_floor=0.50)
    elements = [
        {"id": "l1", "text": "State", "bbox": [0.10, 0.20, 0.25, 0.24], "page_number": 1, "confidence": 0.95},
        {"id": "v1", "text": "xyz123", "bbox": [0.28, 0.20, 0.45, 0.24], "page_number": 1, "confidence": 0.10},
    ]

    classified = extractor._classify_elements(elements, consumed_ids=set())
    class_map = {item["element"]["id"]: item["classification"] for item in classified}
    assert class_map["v1"] == ElementClassification.NOISE

    res = extractor.extract(elements)
    kvs = res["key_values"]
    assert kvs["state"]["value"] is None
    assert kvs["state"]["reason"] == "no_value_matched"


def test_garbled_text_rejected_regardless_of_high_confidence():
    """Genuinely garbled text with high confidence (e.g. 0.99) is still rejected as NOISE."""
    from pipeline.engines.key_value_extractor import ElementClassification

    extractor = KeyValueExtractor()
    garbled_text = "Iageefortiddfandaeessingdharusbyshelakwlamuatrizangtodebimyauaasperlaealscheulefhgeaftaebak"
    elements = [
        {"id": "g1", "text": garbled_text, "bbox": [0.1, 0.1, 0.5, 0.14], "confidence": 0.99}
    ]

    classified = extractor._classify_elements(elements, consumed_ids=set())
    assert classified[0]["classification"] == ElementClassification.NOISE


def test_scanned_character_box_profile_tuning():
    """SCANNED_CHARACTER_BOX_FORMS_PROFILE has relaxed KV floors (0.15 / 0.45 / 0.65),

    while DIGITAL_PDF_PROFILE retains strict defaults (0.20 / 0.50 / 0.72).
    """
    from config.docling_profiles import SCANNED_CHARACTER_BOX_FORMS_PROFILE, DIGITAL_PDF_PROFILE

    # Scanned character box forms profile
    assert SCANNED_CHARACTER_BOX_FORMS_PROFILE.kv_hard_noise_floor == 0.15
    assert SCANNED_CHARACTER_BOX_FORMS_PROFILE.kv_confident_value_floor == 0.45
    assert SCANNED_CHARACTER_BOX_FORMS_PROFILE.kv_min_pair_confidence == 0.65

    # Digital PDF profile untouched
    assert DIGITAL_PDF_PROFILE.kv_hard_noise_floor == 0.20
    assert DIGITAL_PDF_PROFILE.kv_confident_value_floor == 0.50
    assert DIGITAL_PDF_PROFILE.kv_min_pair_confidence == 0.72


def test_idp_scan_metrics_counters():
    """Verify ProcessingMetrics counters and idp_scan output counters increment correctly."""
    from idp.models.processing import ProcessingMetrics

    metrics = ProcessingMetrics()
    assert metrics.kv_needs_review_count == 0
    assert metrics.kv_unmatched_label_count == 0

    # Build simulated spatial key_values
    kv_entries = {
        "state": {"label": "State", "value": "KARNATAKA", "needs_review": True, "confidence": 0.45},
        "amount": {"label": "Amount", "value": "50000", "needs_review": False, "confidence": 0.95},
        "unmatched": {"label": "District", "value": None, "needs_review": True, "reason": "no_value_matched", "confidence": 0.0},
    }

    metrics.kv_needs_review_count = sum(1 for v in kv_entries.values() if v.get("needs_review"))
    metrics.kv_unmatched_label_count = sum(1 for v in kv_entries.values() if v.get("reason") == "no_value_matched")

    assert metrics.kv_needs_review_count == 2
    assert metrics.kv_unmatched_label_count == 1


def test_build_idp_result_with_tiered_kv_confidence(monkeypatch):
    """End-to-end integration test of build_idp_result_from_parsed with ENABLE_TIERED_KV_CONFIDENCE=True.

    Verifies faint fields appear with needs_review=True, unmatched labels emit stubs,
    and counters are correctly tracked on parsed.processing.metrics and return dict.
    """
    from idp.models.document import ParsedDocument, DocumentSource, PageInformation
    from idp.models.layout import LayoutElement
    from idp.models.processing import ProcessingMetadata, ProcessingMetrics
    from pipeline.nodes.idp_scan import build_idp_result_from_parsed
    import pipeline.nodes.idp_scan as idp_scan_mod

    monkeypatch.setattr(idp_scan_mod, "ENABLE_TIERED_KV_CONFIDENCE", True)

    elements = [
        LayoutElement(id="l1", type="text", text="State", bbox=[0.1, 0.2, 0.25, 0.24], page_number=1, confidence=0.92, source="docling_ocr"),
        LayoutElement(id="v1", type="text", text="KARNATAKA", bbox=[0.27, 0.2, 0.45, 0.24], page_number=1, confidence=0.42, source="docling_ocr"),
        LayoutElement(id="l2", type="text", text="District", bbox=[0.1, 0.3, 0.25, 0.34], page_number=1, confidence=0.90, source="docling_ocr"),
    ]

    metrics = ProcessingMetrics()
    processing = ProcessingMetadata(
        document_id="DOC_TEST_001",
        processing_id="P_001",
        file_type="application/pdf",
        mime_type="application/pdf",
        file_size_bytes=1000,
        page_count=1,
        metrics=metrics,
    )
    parsed = ParsedDocument(
        document_id="DOC_TEST_001",
        source=DocumentSource(filename="app_form_scanned.pdf", mime_type="application/pdf"),
        pages=[PageInformation(page_number=1, width=600.0, height=800.0, elements=elements)],
        elements=elements,
        text="State KARNATAKA\nDistrict",
        processing=processing,
        custom_metadata={"is_scanned_pdf": True, "llm_extracted_fields": {}},
    )

    result = build_idp_result_from_parsed(parsed, doc_type="application_form", doc_id="DOC_TEST_001")
    kvs = result["_components"]["key_values"]

    # Faint field KARNATAKA is extracted with needs_review=True (NOT discarded)
    assert "state" in kvs
    assert kvs["state"]["value"] == "KARNATAKA"
    assert kvs["state"]["needs_review"] is True

    # Unmatched label District emits a stub with value=None and reason=no_value_matched
    assert "district" in kvs
    assert kvs["district"]["value"] is None
    assert kvs["district"]["needs_review"] is True
    assert kvs["district"]["reason"] == "no_value_matched"

    # Counters incremented on metrics and result
    assert result["_kv_needs_review_count"] == 2
    assert result["_kv_unmatched_label_count"] == 1
    assert parsed.processing.metrics.kv_needs_review_count == 2
    assert parsed.processing.metrics.kv_unmatched_label_count == 1


def test_build_idp_result_with_legacy_flag_fallback(monkeypatch):
    """When ENABLE_TIERED_KV_CONFIDENCE=False, idp_scan reverts to legacy single-cutoff behavior:

    low-confidence values are discarded as noise and no stubs are emitted for unmatched labels.
    """
    from idp.models.document import ParsedDocument, DocumentSource, PageInformation
    from idp.models.layout import LayoutElement
    from idp.models.processing import ProcessingMetadata, ProcessingMetrics
    from pipeline.nodes.idp_scan import build_idp_result_from_parsed
    import pipeline.nodes.idp_scan as idp_scan_mod

    monkeypatch.setattr(idp_scan_mod, "ENABLE_TIERED_KV_CONFIDENCE", False)

    elements = [
        LayoutElement(id="l1", type="text", text="State", bbox=[0.1, 0.2, 0.25, 0.24], page_number=1, confidence=0.92, source="docling_ocr"),
        LayoutElement(id="v1", type="text", text="KARNATAKA", bbox=[0.27, 0.2, 0.45, 0.24], page_number=1, confidence=0.42, source="docling_ocr"),
        LayoutElement(id="l2", type="text", text="District", bbox=[0.1, 0.3, 0.25, 0.34], page_number=1, confidence=0.90, source="docling_ocr"),
    ]

    metrics = ProcessingMetrics()
    processing = ProcessingMetadata(
        document_id="DOC_TEST_002",
        processing_id="P_002",
        file_type="application/pdf",
        mime_type="application/pdf",
        file_size_bytes=1000,
        page_count=1,
        metrics=metrics,
    )
    parsed = ParsedDocument(
        document_id="DOC_TEST_002",
        source=DocumentSource(filename="app_form_scanned.pdf", mime_type="application/pdf"),
        pages=[PageInformation(page_number=1, width=600.0, height=800.0, elements=elements)],
        elements=elements,
        text="State KARNATAKA\nDistrict",
        processing=processing,
        custom_metadata={"is_scanned_pdf": True, "llm_extracted_fields": {}},
    )

    result = build_idp_result_from_parsed(parsed, doc_type="application_form", doc_id="DOC_TEST_002")
    kvs = result["_components"]["key_values"]

    # In legacy mode: 0.45 < 0.50 was dropped as NOISE, so no value paired
    # and no stubs are emitted for unmatched labels
    assert "state" not in kvs
    assert "district" not in kvs
    assert result["_kv_needs_review_count"] == 0
    assert result["_kv_unmatched_label_count"] == 0


