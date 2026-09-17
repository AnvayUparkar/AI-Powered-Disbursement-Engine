from config.docling_profiles import (
    get_profile_for_document_type,
    IDENTITY_DOCUMENT_PROFILE,
    CHARACTER_BOX_FORMS_PROFILE,
    SCANNED_DOCUMENTS_PROFILE,
    DIGITAL_PDF_PROFILE,
    MIXED_CONTENT_PROFILE,
)


def test_is_scanned_true_overrides_digital_doc_type_hint():
    """A 'loan_agreement' (normally DIGITAL_PDF_PROFILE) that the preprocessor
    actually found to be a scanned copy must get the aggressive scan profile,
    not the native-text-only one that would skip real OCR."""
    profile = get_profile_for_document_type("loan_agreement", is_scanned=True)
    assert profile is SCANNED_DOCUMENTS_PROFILE
    assert profile.force_full_page_ocr is True


def test_is_scanned_false_overrides_scanned_doc_type_hint():
    """A 'bank_statement' that the preprocessor found has a real text layer
    gets DIGITAL_PDF_PROFILE instead of the aggressive scanned profile.
    DIGITAL_PDF_PROFILE now sets force_full_page_ocr=True: Docling's default
    PDF_AWARE_LAYOUT_REGIONS mode skips OCR on regions that already carry
    native PDF text, so comb-box fields on otherwise-digital forms never
    produce per-character tokens and CombBoxDetector never merges them.
    FULL_PAGE OCR is the same reliable path scanned documents already use."""
    profile = get_profile_for_document_type("bank_statement", is_scanned=False)
    assert profile is DIGITAL_PDF_PROFILE
    assert profile.force_full_page_ocr is True


def test_is_scanned_none_falls_back_to_legacy_doc_type_heuristic():
    """Callers that haven't run content inspection (is_scanned=None, the
    default) keep the pre-existing filename/doc-type-based behavior."""
    assert get_profile_for_document_type("bank_statement") is SCANNED_DOCUMENTS_PROFILE
    assert get_profile_for_document_type("salary_slip") is SCANNED_DOCUMENTS_PROFILE
    assert get_profile_for_document_type("loan_agreement") is DIGITAL_PDF_PROFILE
    assert get_profile_for_document_type("sanction_letter") is DIGITAL_PDF_PROFILE
    assert get_profile_for_document_type("nach_mandate") is DIGITAL_PDF_PROFILE
    assert get_profile_for_document_type("unknown_doc_type") is MIXED_CONTENT_PROFILE


def test_structural_profiles_ignore_scan_status():
    """Identity cards and character-box forms are picked for their physical
    layout, not their scan status, so is_scanned must never override them."""
    for is_scanned in (True, False, None):
        assert get_profile_for_document_type("aadhaar", is_scanned=is_scanned) is IDENTITY_DOCUMENT_PROFILE
        assert get_profile_for_document_type("pan_card", is_scanned=is_scanned) is IDENTITY_DOCUMENT_PROFILE
        assert get_profile_for_document_type("application_form", is_scanned=is_scanned) is CHARACTER_BOX_FORMS_PROFILE


def test_is_scanned_true_on_unknown_doc_type_still_uses_scanned_profile():
    """Content inspection takes precedence even for doc types with no
    doc_type-specific branch at all (falls into the former 'default' bucket)."""
    profile = get_profile_for_document_type("some_unrecognized_type", is_scanned=True)
    assert profile is SCANNED_DOCUMENTS_PROFILE


def test_character_box_forms_profile_forces_full_page_ocr():
    """
    Regression: CHARACTER_BOX_FORMS_PROFILE used to declare
    force_full_page_ocr=False ("use native text when available"). Docling's
    OCR mode then stays at its library default, OcrMode.DEFAULT ->
    PDF_AWARE_LAYOUT_REGIONS, which explicitly skips OCR for any layout
    cluster that already contains native PDF text cells (verified against
    the installed docling.datamodel.pipeline_options). A scanned/photographed
    application form has no native text anywhere, so nothing gets skipped
    and every comb-box character still gets OCR'd and merges correctly. A
    genuinely digital PDF's comb-box grid usually DOES have some (often
    mis-segmented, not one-token-per-cell) native text in that region, so it
    got skipped -- CombBoxDetector then never received clean per-character
    tokens, and fields like a name split across two printed cells failed to
    combine. force_full_page_ocr=True forces full OCR regardless of native
    text, making digital documents go through the same reliable per-character
    OCR path scanned documents already use.
    """
    assert CHARACTER_BOX_FORMS_PROFILE.force_full_page_ocr is True


def test_no_profile_declares_non_accurate_table_mode():
    """FAST mode is force-overridden to ACCURATE repo-wide
    (idp/services/docling/pipeline.py's get_cached_converter) and any
    non-ACCURATE value triggers a misleading forced-override warning log at
    converter-build time instead of actually running faster. IDENTITY_DOCUMENT_PROFILE
    used to declare "FAST" (inconsistent with every other profile, and moot
    anyway since it also sets do_table_structure=False) -- guard the whole
    registry so no future profile reintroduces a value that's never honored."""
    from config.docling_profiles import DOCLING_PROFILES

    for name, profile in DOCLING_PROFILES.items():
        assert profile.table_mode.upper() == "ACCURATE", (
            f"profile '{name}' declares table_mode={profile.table_mode!r}, but FAST "
            "mode is disabled repo-wide -- this value is never honored and only "
            "produces a misleading warning log"
        )
