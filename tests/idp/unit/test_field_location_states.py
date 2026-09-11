"""Location states must distinguish real match failures from "nothing to locate".

Previously every non-resolved field was skipped outright, so the UI inferred failure from
the absence of a record and showed "Value was not found in OCR text or tables" for fields
that were never populated, for derived flags that can never appear on a page, and for
documents that produced no OCR at all.
"""
import pytest

from idp.models.extraction import DERIVED_FLAG_FIELDS
from idp.services.extraction.field_location_resolver import FieldLocationResolver

PAGE = [{"width": 600.0, "height": 800.0}]


def _elements():
    return [{
        "id": "e1", "text": "DINESH KUMAR", "bbox": [0.1, 0.1, 0.4, 0.2],
        "page_number": 1, "confidence": 0.95,
        "ocr_confidence": 0.95, "layout_confidence": 0.90,
    }]


def _resolve(fields, elements=None):
    return FieldLocationResolver().resolve_field_locations(
        extracted_fields=fields,
        ocr_elements=_elements() if elements is None else elements,
        page_dimensions=PAGE,
    )


def test_matched_value_is_resolved():
    loc = _resolve({"applicant_name": "DINESH KUMAR"})["applicant_name"]
    assert loc.location_status == "resolved"
    assert loc.bbox is not None


def test_extracted_value_with_no_match_is_unresolved():
    """The only state that is genuinely a location failure."""
    loc = _resolve({"emi": "99999"})["emi"]
    assert loc.location_status == "unresolved"
    assert "no sufficiently confident match" in loc.reason


@pytest.mark.parametrize("value", [None, "", "   ", "None"])
def test_absent_value_is_not_extracted_not_a_failure(value):
    loc = _resolve({"loan_type": value})["loan_type"]
    assert loc.location_status == "not_extracted"
    assert "nothing to locate" in loc.reason
    assert loc.bbox is None


@pytest.mark.parametrize("field", sorted(DERIVED_FLAG_FIELDS))
def test_derived_flags_are_not_locatable(field):
    """These are computed from document checks and never appear as page text."""
    loc = _resolve({field: True})[field]
    assert loc.location_status == "not_locatable"
    assert "Derived flag" in loc.reason


@pytest.mark.parametrize("value", [True, False])
def test_any_boolean_is_treated_as_derived(value):
    loc = _resolve({"some_flag": value})["some_flag"]
    assert loc.location_status == "not_locatable"


def test_document_with_no_ocr_reports_no_ocr_text():
    """Distinguishes 'we searched and failed' from 'there was nothing to search'."""
    loc = _resolve({"applicant_name": "DINESH KUMAR"}, elements=[])["applicant_name"]
    assert loc.location_status == "no_ocr_text"
    assert "no OCR tokens" in loc.reason


def test_internal_keys_are_still_skipped_entirely():
    """Bookkeeping keys are not user-facing fields and must not appear in the panel."""
    assert "_components" not in _resolve({"_components": {"a": 1}, "applicant_name": "DINESH KUMAR"})


def test_every_field_now_gets_a_record():
    """Regression: the panel should never have to infer a state from a missing entry."""
    fields = {
        "applicant_name": "DINESH KUMAR",   # resolved
        "emi": "99999",                      # unresolved
        "loan_type": None,                   # not_extracted
        "aadhaar_xml_present": False,        # not_locatable
    }
    out = _resolve(fields)
    assert set(out) == set(fields)
    assert {loc.location_status for loc in out.values()} == {
        "resolved", "unresolved", "not_extracted", "not_locatable",
    }
