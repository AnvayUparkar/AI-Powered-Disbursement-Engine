"""Profile selection is deliberately no longer document-type-dependent.

The six-profile system (identity/character-box/scanned/digital/mixed/
high-performance) has been replaced with a single OCR_FIRST_PROFILE that
always runs full-page OCR, regardless of document type or whether the
preprocessor found a native text layer. See config/docling_profiles.py's
module docstring for why: trusting a PDF's embedded text (force_full_page_ocr
=False on the old DIGITAL_PDF_PROFILE) meant a broken font's ToUnicode
mapping was never re-verified via OCR, which is exactly what caused digital
PDFs to extract worse than scanned/handwritten ones.
"""
import pytest

from config.docling_profiles import (
    DOCLING_PROFILES,
    OCR_FIRST_PROFILE,
    get_profile,
    get_profile_for_document_type,
)


@pytest.mark.parametrize(
    "doc_type", ["aadhaar", "pan", "application_form", "bank_statement",
                 "loan_agreement", "sanction_letter", "kfs", "unknown_doc_type"]
)
@pytest.mark.parametrize("is_scanned", [True, False, None])
def test_every_doc_type_and_scan_status_resolves_to_the_same_profile(doc_type, is_scanned):
    """Neither argument affects routing any more -- both are accepted purely
    for call-site compatibility with idp/services/document_processor.py."""
    assert get_profile_for_document_type(doc_type, is_scanned=is_scanned) is OCR_FIRST_PROFILE


def test_get_profile_by_name_still_works():
    assert get_profile("ocr_first") is OCR_FIRST_PROFILE


def test_unknown_profile_name_raises_key_error():
    with pytest.raises(KeyError):
        get_profile("digital_pdf")  # the old name no longer exists


def test_registry_has_exactly_one_profile():
    assert list(DOCLING_PROFILES.keys()) == ["ocr_first"]


def test_ocr_first_profile_always_forces_full_page_ocr():
    """The entire point of the collapse: no document, digital or scanned,
    gets to skip OCR by trusting its own embedded/native text."""
    assert OCR_FIRST_PROFILE.force_full_page_ocr is True


def test_ocr_first_profile_uses_lowest_bbox_sensitivity_thresholds():
    """Every threshold governing whether a region/box survives is pushed to
    its most permissive value, trading precision for recall."""
    assert OCR_FIRST_PROFILE.layout_detection_threshold <= 0.05
    assert OCR_FIRST_PROFILE.ocr_text_score <= 0.05
    assert OCR_FIRST_PROFILE.rapidocr_params["Det.thresh"] <= 0.05
    assert OCR_FIRST_PROFILE.rapidocr_params["Det.box_thresh"] <= 0.1
    assert OCR_FIRST_PROFILE.table_confidence_threshold <= 0.05
    assert OCR_FIRST_PROFILE.table_min_rows == 1
    assert OCR_FIRST_PROFILE.table_min_cols == 1


def test_ocr_first_profile_uses_cell_matching_so_image_uploads_get_real_table_text():
    """do_cell_matching=False would read table-cell text via
    backend.get_text_in_rect(), which is hardcoded to return "" for raw image
    uploads (no native text layer at all) -- do_cell_matching=True instead
    reads the OCR-populated parsed_page cells, which force_full_page_ocr
    guarantees exist for every input type, images included."""
    assert OCR_FIRST_PROFILE.do_cell_matching is True


def test_no_profile_declares_non_accurate_table_mode():
    """FAST mode is force-overridden to ACCURATE repo-wide
    (idp/services/docling/pipeline.py's get_cached_converter) and any
    non-ACCURATE value triggers a misleading forced-override warning log."""
    for name, profile in DOCLING_PROFILES.items():
        assert profile.table_mode.upper() == "ACCURATE", (
            f"profile '{name}' declares table_mode={profile.table_mode!r}, but FAST "
            "mode is disabled repo-wide -- this value is never honored"
        )
