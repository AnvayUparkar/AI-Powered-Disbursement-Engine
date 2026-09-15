"""use_gpu / num_threads / OCR detection knobs must actually reach Docling.

use_gpu and num_threads were set on every profile but read nowhere, so Docling silently
used its own default of device="auto". Likewise det_limit_side_len / det_db_thresh /
det_db_box_thresh / rec_batch_num were assigned behind hasattr guards for attributes that
do not exist on RapidOcrOptions, so every value configured for them was discarded.

DocumentConverter is stubbed, so nothing here loads a model or touches the network.
"""
import pytest
from docling.datamodel.accelerator_options import AcceleratorDevice
from docling.datamodel.pipeline_options import OcrMode
from docling.utils.accelerator_utils import decide_device

from idp.services.docling.options import DoclingOptions
from idp.services.docling.pipeline import (
    _get_options_key,
    get_cached_converter,
    invalidate_converter_cache,
)


@pytest.fixture
def captured(monkeypatch):
    import docling.document_converter as dc

    box: dict = {}

    class _Stub:
        def __init__(self, format_options=None, **kwargs):
            box["format_options"] = format_options

    monkeypatch.setattr(dc, "DocumentConverter", _Stub)
    invalidate_converter_cache()
    yield box
    invalidate_converter_cache()


def _pdf_opts(captured):
    return captured["format_options"]["pdf"].pipeline_options


def test_use_gpu_true_selects_auto_device(captured):
    get_cached_converter(DoclingOptions(use_gpu=True))
    assert _pdf_opts(captured).accelerator_options.device is AcceleratorDevice.AUTO


def test_use_gpu_false_pins_cpu(captured):
    """False must genuinely pin CPU, not fall through to Docling's auto default."""
    get_cached_converter(DoclingOptions(use_gpu=False))
    acc = _pdf_opts(captured).accelerator_options
    assert acc.device is AcceleratorDevice.CPU
    assert decide_device(acc.device) == "cpu"


@pytest.mark.parametrize("threads", [1, 4, 8])
def test_num_threads_reaches_the_accelerator(captured, threads):
    get_cached_converter(DoclingOptions(num_threads=threads))
    assert _pdf_opts(captured).accelerator_options.num_threads == threads


def test_ocr_text_score_is_applied(captured):
    """The working replacement for the non-existent det_db_box_thresh."""
    get_cached_converter(DoclingOptions(do_ocr=True, ocr_text_score=0.2))
    assert _pdf_opts(captured).ocr_options.text_score == pytest.approx(0.2)


def test_rapidocr_params_passthrough(captured):
    get_cached_converter(DoclingOptions(do_ocr=True, rapidocr_params={"det_db_thresh": 0.15}))
    assert _pdf_opts(captured).ocr_options.rapidocr_params == {"det_db_thresh": 0.15}


def test_empty_rapidocr_params_leaves_engine_defaults(captured):
    """Edge: an empty dict must not clobber whatever RapidOcrOptions defaults to."""
    get_cached_converter(DoclingOptions(do_ocr=True, rapidocr_params={}))
    assert _pdf_opts(captured).ocr_options.rapidocr_params == {}


def test_image_format_inherits_the_accelerator(captured):
    """The image converter is a deep copy and must carry the same device."""
    get_cached_converter(DoclingOptions(use_gpu=False))
    image_opts = captured["format_options"]["image"].pipeline_options
    assert image_opts.accelerator_options.device is AcceleratorDevice.CPU
    assert image_opts.ocr_options.mode is OcrMode.FULL_PAGE


@pytest.mark.parametrize(
    "a,b",
    [
        (DoclingOptions(use_gpu=True), DoclingOptions(use_gpu=False)),
        (DoclingOptions(num_threads=4), DoclingOptions(num_threads=8)),
        (DoclingOptions(ocr_text_score=0.5), DoclingOptions(ocr_text_score=0.2)),
        (DoclingOptions(rapidocr_params={}), DoclingOptions(rapidocr_params={"x": 1})),
    ],
)
def test_options_affecting_the_build_are_in_the_cache_key(a, b):
    """Two profiles differing only by these must not share one cached converter."""
    assert _get_options_key(a) != _get_options_key(b)


def test_every_shipped_profile_resolves_to_a_real_device():
    """Guards the flip to use_gpu=True: wiring a dead flag must not silently pin CPU."""
    from config.docling_profiles import DOCLING_PROFILES

    for name, profile in DOCLING_PROFILES.items():
        device = AcceleratorDevice.AUTO if profile.use_gpu else AcceleratorDevice.CPU
        assert decide_device(device) in {"cpu", "mps", "cuda", "xpu"}, name
        assert profile.use_gpu is True, (
            f"{name} pins CPU; Docling previously defaulted to auto, so this is a regression"
        )
