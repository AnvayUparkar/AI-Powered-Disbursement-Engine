import pytest
from idp.services.ocr.confidence import OCRConfidenceEvaluator
from pipeline.nodes.key_value_extractor import KeyValueExtractor, ElementClassification


def test_pan_number_not_flagged_as_garbled():
    """Verify that valid PAN numbers and other financial identifiers are not flagged as garbled text."""
    evaluator = OCRConfidenceEvaluator()

    # Happy path: PAN with 0 vowels in letters (C, F, V, P, M, Q)
    assert evaluator.is_garbled_text("CFVPM7810Q") is False

    # Happy path: Standard PANs, IFSC, GSTIN
    assert evaluator.is_garbled_text("ABCDE1234F") is False
    assert evaluator.is_garbled_text("HDFC0001234") is False
    assert evaluator.is_garbled_text("27ABCDE1234F1Z5") is False

    # Contextual PAN with label
    assert evaluator.is_garbled_text("PermanentAccountNumber CFVPM7810Q") is False
    assert evaluator.is_garbled_text("PAN CFVPM 7810Q") is False


def test_garbled_noise_still_rejected():
    """Verify that actual OCR noise and consonant gibberish are still caught."""
    evaluator = OCRConfidenceEvaluator()

    # Consonant gibberish
    assert evaluator.is_garbled_text("HRTRR") is True
    assert evaluator.is_garbled_text("RROR") is True

    # Corrupted math / Greek symbol noise
    assert evaluator.is_garbled_text("3T9T3πT&T") is True
    assert evaluator.is_garbled_text("παβγδε") is True


def test_edge_cases_empty_and_corrupted():
    """Edge cases: empty strings, pure symbols, boundary conditions."""
    evaluator = OCRConfidenceEvaluator()

    assert evaluator.is_garbled_text("") is False
    assert evaluator.is_garbled_text("   ") is False
    assert evaluator.is_garbled_text("@#$%^&*") is True

    # Corrupted symbol noise mixed with PAN candidate should still be flagged
    assert evaluator.is_garbled_text("CFVPM7810Qπ") is True


def test_key_value_extractor_pan_classification():
    """Verify PermanentAccountNumber is classified as LABEL, not CHECKBOX_LABEL."""
    extractor = KeyValueExtractor()

    elements = [
        {
            "id": "elem-pan-lbl",
            "text": "PermanentAccountNumber",
            "bbox": [0.034, 0.630, 0.390, 0.674],
            "confidence": 0.88,
            "page_number": 1
        },
        {
            "id": "elem-pan-val",
            "text": "CFVPM7810Q",
            "bbox": [0.035, 0.690, 0.300, 0.730],
            "confidence": 0.89,
            "page_number": 1
        }
    ]

    classified = extractor._classify_elements(elements, consumed_ids=set())
    class_map = {item["element"]["id"]: item["classification"] for item in classified}

    assert class_map["elem-pan-lbl"] == ElementClassification.LABEL
    assert class_map["elem-pan-val"] == ElementClassification.VALUE

    # Spatial pairing
    result = extractor.extract(elements, doc_type="kyc_pan")
    kv = result["key_values"].get("permanentaccountnumber") or result["key_values"].get("permanent_account_number")
    assert kv is not None
    assert kv["value"] == "CFVPM7810Q"
    assert kv["relationship"] == "below_label"


def test_checkbox_option_word_boundaries():
    """Verify that words like 'account' or 'disbursement' are not falsely treated as checkbox options."""
    extractor = KeyValueExtractor()

    # Should NOT be classified as checkbox label
    account_elem = [{"id": "elem-acc", "text": "Bank Account Number", "bbox": [0.1, 0.3, 0.4, 0.35], "confidence": 0.95}]
    classified = extractor._classify_elements(account_elem, consumed_ids=set())
    assert classified[0]["classification"] == ElementClassification.LABEL

    # Actual checkbox option string SHOULD be classified as checkbox label
    cb_elem = [{"id": "elem-cb", "text": "SB / CA / CC", "bbox": [0.1, 0.2, 0.3, 0.25], "confidence": 0.95}]
    classified_cb = extractor._classify_elements(cb_elem, consumed_ids=set())
    assert classified_cb[0]["classification"] == ElementClassification.CHECKBOX_LABEL
