"""GPU/CPU device selection for the Docling pipeline.

Two things this centralizes that the inline ``AcceleratorOptions(...)`` in
pipeline.py did not:

1. **Explicit device pinning.** ``use_gpu=True`` previously mapped to
   ``AcceleratorDevice.AUTO`` unconditionally, which is right on a CUDA box but
   gives an operator no way to say "this worker uses cuda:1" or "pin MPS, never
   silently degrade to CPU". ``IDP_ACCELERATOR_DEVICE`` now accepts
   auto/cuda/cuda:N/mps/cpu and overrides the profile.

2. **Thread-count sanity.** ``num_threads`` is not CPU-only bookkeeping -- Docling
   forwards it to RapidOCR as ``EngineConfig.onnxruntime.intra_op_num_threads``
   (see docling.models.stages.ocr.rapid_ocr_model), and OCR is the single most
   expensive stage in this pipeline. Requesting more intra-op threads than the
   machine has cores oversubscribes the ONNX Runtime thread pool, so the
   configured value is clamped to the real core count with a warning.

What device selection does and does not buy, measured on this repo's hardware:
the layout model and TableFormer are torch and genuinely run on the selected
accelerator (MPS on Apple silicon, CUDA on Linux). RapidOCR runs on ONNX
Runtime, and Docling only ever sets ``use_cuda``/``use_dml`` for it -- so on
macOS, OCR stays on the CPU execution provider no matter what is configured
here. CoreML was benchmarked as an alternative and was ~2x SLOWER than the CPU
provider, so it is deliberately not enabled. The device setting therefore only
moves the needle on a CUDA host, where ``use_cuda`` actually engages and OCR
moves onto the GPU along with the torch models.
"""

import os
from typing import Any, Optional, Tuple

from idp.core.logging import logger

ENV_DEVICE = "IDP_ACCELERATOR_DEVICE"

# Accepted IDP_ACCELERATOR_DEVICE values (case-insensitive). "cuda:N" is also
# accepted; Docling parses the index off the string itself.
_VALID_DEVICES = ("auto", "cuda", "mps", "cpu", "xpu")


def _cpu_count() -> int:
    return os.cpu_count() or 1


def resolve_num_threads(num_threads: int) -> int:
    """Clamp a configured thread count to [1, cpu_count].

    Oversubscribing ONNX Runtime's intra-op pool (num_threads > cores) adds
    context-switching cost to the OCR stage without adding parallelism.
    """
    cores = _cpu_count()
    if num_threads < 1:
        logger.warning(
            "[Accelerator] num_threads=%s is invalid; using 1.", num_threads
        )
        return 1
    if num_threads > cores:
        logger.warning(
            "[Accelerator] num_threads=%s exceeds the %s available CPU cores; "
            "clamping to %s to avoid oversubscribing the ONNX Runtime intra-op "
            "thread pool used by OCR.", num_threads, cores, cores
        )
        return cores
    return num_threads


def _device_from_env() -> Optional[str]:
    raw = (os.getenv(ENV_DEVICE) or "").strip().lower()
    if not raw:
        return None
    base = raw.split(":")[0]
    if base not in _VALID_DEVICES:
        logger.warning(
            "[Accelerator] %s=%r is not one of %s; ignoring the override.",
            ENV_DEVICE, raw, ", ".join(_VALID_DEVICES),
        )
        return None
    return raw


def resolve_device(use_gpu: bool) -> Any:
    """Resolve the AcceleratorDevice to request.

    ``IDP_ACCELERATOR_DEVICE`` wins when set and valid; otherwise ``use_gpu``
    selects AUTO (best available: CUDA > MPS > XPU > CPU) or pins CPU.
    """
    from docling.datamodel.accelerator_options import AcceleratorDevice

    env_device = _device_from_env()
    if env_device is not None:
        # Returned as a plain string: AcceleratorOptions validates these itself
        # and preserves a device index, so "cuda:1" survives intact (an
        # AcceleratorDevice enum member could only ever express "cuda").
        logger.info(
            "[Accelerator] Device pinned to %r via %s (profile use_gpu=%s ignored).",
            env_device, ENV_DEVICE, use_gpu,
        )
        return env_device

    return AcceleratorDevice.AUTO if use_gpu else AcceleratorDevice.CPU


def build_accelerator_options(use_gpu: bool, num_threads: int) -> Tuple[Any, str]:
    """Build AcceleratorOptions and report the hardware Docling actually resolved to.

    Returns:
        (AcceleratorOptions, effective_device_string) where the second element is
        the concrete device Docling's own decide_device() picked -- e.g. "mps",
        "cuda:0" or "cpu" -- which is what should be logged, since AUTO alone
        does not tell an operator whether a GPU was really found.
    """
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.utils.accelerator_utils import decide_device

    device = resolve_device(use_gpu)
    threads = resolve_num_threads(num_threads)
    options = AcceleratorOptions(device=device, num_threads=threads)

    try:
        effective = decide_device(options.device)
    except Exception as exc:  # pragma: no cover - depends on the torch build
        logger.warning("[Accelerator] Could not resolve effective device: %s", exc)
        effective = str(options.device)

    if use_gpu and effective == "cpu":
        logger.warning(
            "[Accelerator] use_gpu=True but no GPU was found; running on CPU."
        )
    logger.info(
        "[Accelerator] device=%s -> effective=%s, num_threads=%s (of %s cores). "
        "Layout + TableFormer run on this device; RapidOCR follows it only on CUDA.",
        options.device, effective, threads, _cpu_count(),
    )
    return options, effective
