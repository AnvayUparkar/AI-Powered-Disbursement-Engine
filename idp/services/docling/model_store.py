"""Local resolution of Docling's ML weights (layout, TableFormer, RapidOCR).

By default Docling resolves every model through the HuggingFace hub cache
(``~/.cache/huggingface/hub``) and RapidOCR downloads its ONNX checkpoints
straight into ``site-packages/rapidocr/models``. Both are a problem in
production: the site-packages copy is destroyed on every venv rebuild, and
both paths require outbound network access on first use of a cold worker.

Docling exposes exactly one switch for this: ``PdfPipelineOptions.artifacts_path``.
When set, the layout model, TableFormer and RapidOCR all load from that
directory and perform no network access at all ("artifacts_path means fully
offline operation" -- docling.models.stages.ocr.rapid_ocr_model). The catch is
that it is strict: if a single expected file is absent under that directory,
model construction raises rather than falling back, so this module refuses to
hand out an artifacts_path that is not actually populated -- unless the
operator has explicitly demanded offline operation, in which case the missing
weights are a fail-fast configuration error rather than something to paper over.

Populate the directory with ``python scripts/download_models.py``.
"""

import os
from pathlib import Path
from typing import List, Optional

from config.paths import MODELS_DIR
from idp.core.logging import logger

# Default location for the downloaded weights. Overridable so a container image
# can bake them at an absolute path outside the source tree.
DEFAULT_ARTIFACTS_DIR: Path = MODELS_DIR / "docling"

# Environment overrides.
ENV_ARTIFACTS_PATH = "DOCLING_ARTIFACTS_PATH"
ENV_OFFLINE = "DOCLING_OFFLINE"

# Subdirectories that download_models() creates under the artifacts root. These
# are Docling's own folder names (TableStructureModel._model_repo_folder,
# RapidOcrModel._model_repo_folder and the layout repo ids with "/" replaced by
# "--"), hardcoded here so a presence check does not need to import and
# instantiate Docling's model classes.
_REQUIRED_SUBDIRS: tuple = (
    "docling-project--docling-layout-heron",
    "docling-project--docling-models",  # TableFormer
    "RapidOcr",
)


def _is_truthy(raw: Optional[str]) -> bool:
    return (raw or "").strip().lower() in ("true", "1", "yes")


def is_offline_required() -> bool:
    """True when the operator has demanded fully-local weights (DOCLING_OFFLINE)."""
    return _is_truthy(os.getenv(ENV_OFFLINE))


def get_default_artifacts_dir() -> Path:
    """Where weights live: $DOCLING_ARTIFACTS_PATH, else <repo>/models/docling."""
    override = (os.getenv(ENV_ARTIFACTS_PATH) or "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_ARTIFACTS_DIR


def missing_artifacts(artifacts_dir: Path) -> List[str]:
    """Return the names of expected model subdirectories that are absent or empty.

    An empty list means the directory is usable as an ``artifacts_path``.
    """
    if not artifacts_dir.is_dir():
        return list(_REQUIRED_SUBDIRS)
    missing = []
    for name in _REQUIRED_SUBDIRS:
        sub = artifacts_dir / name
        if not sub.is_dir() or not any(sub.iterdir()):
            missing.append(name)
    return missing


def resolve_artifacts_path(explicit: Optional[str] = None) -> Optional[Path]:
    """Resolve the artifacts_path to hand to Docling, or None to use the HF cache.

    Args:
        explicit: Path from the active DoclingOptions profile. Takes precedence
            over the environment default when set.

    Returns:
        A populated directory to load all weights from, or None when no local
        weights are available (Docling then falls back to its HuggingFace cache
        and downloads on demand).

    Raises:
        FileNotFoundError: If DOCLING_OFFLINE is set but the weights are not
            present -- silently falling back to a network download is exactly
            what offline mode exists to prevent.
    """
    artifacts_dir = Path(explicit).expanduser() if explicit else get_default_artifacts_dir()
    missing = missing_artifacts(artifacts_dir)

    if not missing:
        logger.info("[ModelStore] Loading Docling weights locally from %s", artifacts_dir)
        return artifacts_dir

    detail = (
        f"Local Docling weights incomplete at {artifacts_dir} "
        f"(missing: {', '.join(missing)}). Run: python scripts/download_models.py"
    )
    if is_offline_required():
        raise FileNotFoundError(
            f"{ENV_OFFLINE} is set but no usable local weights were found. {detail}"
        )

    logger.warning(
        "[ModelStore] %s -- falling back to the HuggingFace cache (models will be "
        "downloaded on first use).", detail
    )
    return None


def download_models(
    artifacts_dir: Optional[Path] = None,
    *,
    force: bool = False,
    progress: bool = True,
) -> Path:
    """Download the layout, TableFormer and RapidOCR weights into artifacts_dir.

    Only the three model families this pipeline actually runs are fetched;
    Docling's VLM/picture-description/code-formula models are several GB and
    are never used by DoclingParser, so they are explicitly disabled.

    Returns:
        The directory the weights were written to.
    """
    from docling.utils.model_downloader import download_models as _docling_download

    target = Path(artifacts_dir) if artifacts_dir else get_default_artifacts_dir()
    target.mkdir(parents=True, exist_ok=True)
    logger.info("[ModelStore] Downloading Docling weights into %s (force=%s)", target, force)

    _docling_download(
        output_dir=target,
        force=force,
        progress=progress,
        with_layout=True,
        with_tableformer=True,
        with_rapidocr=True,
        # Not used anywhere in this pipeline -- skipped to keep the payload small.
        with_code_formula=False,
        with_picture_classifier=False,
        with_easyocr=False,
    )

    missing = missing_artifacts(target)
    if missing:
        raise RuntimeError(
            f"Download completed but {target} is still missing: {', '.join(missing)}"
        )
    logger.info("[ModelStore] Docling weights ready at %s", target)
    return target
