"""Format-option registration for the Docling DocumentConverter.

Raw images resolve to InputFormat.IMAGE, a different key from "pdf". Registering
only the PDF entry left image uploads running on Docling's stock defaults, so none
of the DoclingOptions profile (images_scale, layout threshold, TableFormer mode,
RapidOCR engine) reached them. These tests pin the registration contract.

DocumentConverter is stubbed out so nothing here loads an ONNX model or touches the
network; the assertions are on the format_options actually handed to it.
"""
import pytest
from docling.datamodel.pipeline_options import OcrMode

from idp.services.docling.options import DoclingOptions
from idp.services.docling.pipeline import get_cached_converter, invalidate_converter_cache


@pytest.fixture
def captured_format_options(monkeypatch):
    """Replace DocumentConverter with a stub that records its format_options kwarg."""
    import docling.document_converter as dc

    captured: dict = {}

    class _StubConverter:
        def __init__(self, format_options=None, **kwargs):
            captured["format_options"] = format_options

    monkeypatch.setattr(dc, "DocumentConverter", _StubConverter)
    invalidate_converter_cache()
    yield captured
    invalidate_converter_cache()


def _opts_for(captured, key):
    return captured["format_options"][key].pipeline_options


def test_both_pdf_and_image_formats_are_registered(captured_format_options):
    """Happy path: an image upload must not fall through to Docling's stock defaults."""
    get_cached_converter(DoclingOptions())

    registered = captured_format_options["format_options"]
    assert set(registered) == {"pdf", "image"}


def test_image_format_uses_full_page_ocr_while_pdf_does_not(captured_format_options):
    """Images have no text layer, so cluster-based region selection must be bypassed."""
    get_cached_converter(DoclingOptions(do_ocr=True, force_full_page_ocr=False))

    assert _opts_for(captured_format_options, "image").ocr_options.mode is OcrMode.FULL_PAGE
    assert _opts_for(captured_format_options, "pdf").ocr_options.mode is OcrMode.DEFAULT


def test_image_format_inherits_the_profile_tuning(captured_format_options):
    """The whole point of registering the format: profile values must reach images."""
    get_cached_converter(DoclingOptions(images_scale=3.0, layout_detection_threshold=0.05))

    image_opts = _opts_for(captured_format_options, "image")
    assert image_opts.images_scale == 3.0
    assert image_opts.layout_options.engine_options.score_threshold == 0.05


def test_pdf_options_are_not_mutated_by_the_image_copy(captured_format_options):
    """Regression guard: the image variant is a deep copy, never a shared reference."""
    get_cached_converter(DoclingOptions(do_ocr=True, force_full_page_ocr=True))

    pdf_opts = _opts_for(captured_format_options, "pdf")
    image_opts = _opts_for(captured_format_options, "image")

    assert pdf_opts is not image_opts
    assert pdf_opts.ocr_options is not image_opts.ocr_options
    # force_full_page_ocr=True means the PDF side is FULL_PAGE on its own merit;
    # both being FULL_PAGE here must come from the profile, not from aliasing.
    assert pdf_opts.ocr_options.mode is OcrMode.FULL_PAGE


def test_ocr_disabled_still_registers_both_formats(captured_format_options):
    """Edge case: with do_ocr=False no RapidOcrOptions is built; must not raise."""
    get_cached_converter(DoclingOptions(do_ocr=False))

    registered = captured_format_options["format_options"]
    assert set(registered) == {"pdf", "image"}
    assert _opts_for(captured_format_options, "pdf").do_ocr is False
    assert _opts_for(captured_format_options, "image").do_ocr is False


def test_converter_construction_failure_falls_back_to_mock(monkeypatch):
    """Failure mode: a broken Docling install must degrade, not crash the processor."""
    import docling.document_converter as dc

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated missing ONNX runtime")

    monkeypatch.setattr(dc, "DocumentConverter", _boom)
    invalidate_converter_cache()
    try:
        assert get_cached_converter(DoclingOptions()) == "MOCK"
    finally:
        invalidate_converter_cache()
