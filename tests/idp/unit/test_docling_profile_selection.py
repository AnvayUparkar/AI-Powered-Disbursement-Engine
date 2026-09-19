from config.docling_profiles import (
    get_profile_for_document_type,
    IDENTITY_DOCUMENT_PROFILE,
    CHARACTER_BOX_FORMS_PROFILE,
    SCANNED_CHARACTER_BOX_FORMS_PROFILE,
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
    DIGITAL_PDF_PROFILE keeps force_full_page_ocr=False (uses native PDF text
    for digital documents, avoids unnecessary full re-OCR)."""
    profile = get_profile_for_document_type("bank_statement", is_scanned=False)
    assert profile is DIGITAL_PDF_PROFILE
    assert profile.force_full_page_ocr is False


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
    """Identity cards are always IDENTITY_DOCUMENT_PROFILE regardless of scan
    status. application_form has two sub-profiles: scanned gets
    SCANNED_CHARACTER_BOX_FORMS_PROFILE (force_full_page_ocr=True +
    char-box structural settings); digital/uninspected gets
    CHARACTER_BOX_FORMS_PROFILE (force_full_page_ocr=False)."""
    for is_scanned in (True, False, None):
        assert get_profile_for_document_type("aadhaar", is_scanned=is_scanned) is IDENTITY_DOCUMENT_PROFILE
        assert get_profile_for_document_type("pan_card", is_scanned=is_scanned) is IDENTITY_DOCUMENT_PROFILE
    # Scanned application form → scanned character-box profile
    assert get_profile_for_document_type("application_form", is_scanned=True) is SCANNED_CHARACTER_BOX_FORMS_PROFILE
    # Digital or uninspected → digital character-box profile
    assert get_profile_for_document_type("application_form", is_scanned=False) is CHARACTER_BOX_FORMS_PROFILE
    assert get_profile_for_document_type("application_form", is_scanned=None) is CHARACTER_BOX_FORMS_PROFILE

def test_is_scanned_true_on_unknown_doc_type_still_uses_scanned_profile():
    """Content inspection takes precedence even for doc types with no
    doc_type-specific branch at all (falls into the former 'default' bucket)."""
    profile = get_profile_for_document_type("some_unrecognized_type", is_scanned=True)
    assert profile is SCANNED_DOCUMENTS_PROFILE


def test_character_box_forms_profile_ocr_mode():
    """
    CHARACTER_BOX_FORMS_PROFILE keeps force_full_page_ocr=False: this profile
    is digital-only (is_scanned=False or None). Scanned application forms are
    routed to SCANNED_DOCUMENTS_PROFILE before this profile is ever returned,
    so the full-page OCR guarantee for scanned forms is met via routing.
    """
    assert CHARACTER_BOX_FORMS_PROFILE.force_full_page_ocr is False
    # The scan path gets full-page OCR through routing, not this profile
    assert SCANNED_DOCUMENTS_PROFILE.force_full_page_ocr is True


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
