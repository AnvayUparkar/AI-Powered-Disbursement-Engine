"""Guard against clean_bilingual_label_noise() deleting whole identifiers.

The bilingual noise rules target short, unstructured Devanagari-misread fragments.
Rule 6 (`[A-Z]{2,}\\d+\\s?\\d*`) is broad enough to consume real financial identifiers
outright -- IFSC codes, cheque/account references, PAN-shaped tokens. When the
sanitizer returns "", DocumentSerializer drops the element entirely, so the text AND
its bounding box vanish from the extracted document and the reviewer UI.
"""
import pytest

from idp.services.ocr.confidence import OCRConfidenceEvaluator


@pytest.mark.parametrize(
    "token",
    [
        "HDFC0000123",   # IFSC
        "SBIN0001234",   # IFSC
        "CHQ123456",     # cheque reference
        "AC1234567890",  # account reference
        "HAGP1388",      # PAN-shaped fragment (handwritten form)
        "HAPM4222",
    ],
)
def test_mixed_alphanumeric_identifiers_are_never_annihilated(token):
    """A token the rules consume completely must survive rather than become ''."""
    assert OCRConfidenceEvaluator.clean_bilingual_label_noise(token) == token


def test_valid_pan_is_untouched():
    """Regression: a well-formed PAN never matched rule 6 and must stay byte-identical."""
    assert OCRConfidenceEvaluator.clean_bilingual_label_noise("ABCDE1234F") == "ABCDE1234F"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("aT&/Signature", "Signature"),          # prefix stripping still applies
        ("fua/Father's Name", "Father's Name"),
        ("GR@/DOB", "DOB"),
    ],
)
def test_prefix_stripping_still_works(text, expected):
    """The guard must only fire on total annihilation, never weaken partial cleaning."""
    assert OCRConfidenceEvaluator.clean_bilingual_label_noise(text) == expected


def test_identifier_inside_a_phrase_survives_intact():
    """An IFSC embedded in surrounding text must not be stripped out of the phrase."""
    out = OCRConfidenceEvaluator.clean_bilingual_label_noise("Account HDFC0000123")
    assert out == "Account HDFC0000123"


def test_partial_cleaning_is_not_reverted():
    """A string only partly stripped keeps the cleaned form, not the original."""
    out = OCRConfidenceEvaluator.clean_bilingual_label_noise("aT&/Signature 3TET")
    assert out != "aT&/Signature 3TET"
    assert "Signature" in out
    assert "3TET" not in out


@pytest.mark.parametrize("identifier", ["HDFC0000123", "SBIN0001234", "ABCDE1234F"])
def test_identifiers_are_not_flagged_as_garbled(identifier):
    """is_garbled_text carries its own copy of the misread regexes; it must exempt too.

    A True here makes DocumentSerializer drop the element and its bbox entirely.
    """
    assert OCRConfidenceEvaluator().is_garbled_text(identifier) is False


@pytest.mark.parametrize("noise", ["3TET", "334AB", "HRTRR12"])
def test_devanagari_misreads_are_still_flagged(noise):
    """The exemption must not blunt the rule for its actual targets."""
    assert OCRConfidenceEvaluator().is_garbled_text(noise) is True


@pytest.mark.parametrize("token", ["TE", "RAA", "HOTRT", "Wuy"])
def test_letter_only_noise_tokens_are_still_removed(token):
    """The guard requires BOTH a letter and a digit, so pure-letter noise is unaffected."""
    assert OCRConfidenceEvaluator.clean_bilingual_label_noise(token) == ""


@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_and_none_inputs_do_not_raise(value):
    """Edge case: falsy input returns unchanged without hitting the guard's regexes."""
    assert OCRConfidenceEvaluator.clean_bilingual_label_noise(value) == value
