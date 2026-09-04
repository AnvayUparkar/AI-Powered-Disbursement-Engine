import pytest
from pipeline.nodes.key_value_extractor import KeyValueExtractor


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

    assert "reference" not in kvs
