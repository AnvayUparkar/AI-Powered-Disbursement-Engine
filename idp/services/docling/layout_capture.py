"""Capture the layout model's RAW cluster predictions for the debug overlay.

Docling discards these. The layout stage emits raw clusters, then
LayoutPostprocessor rewrites them -- merging, dropping, and snapping each
surviving cluster's bbox to the union of the RapidOCR cells it contains -- and
``page.predictions.layout`` is REPLACED with the processed result
(docling/models/stages/layout/layout_postprocessing_model.py). The raw
prediction only ever escapes as a debug PNG via ``visualize_raw_layout``, never
as data.

That loss is exactly what makes "why is this bbox missing / the wrong size?"
hard to answer. On a representative page of this repo's own test documents the
layout model proposes ~300 clusters and postprocessing keeps ~70, reshaping 59
of those 70. Without the raw set there is no way to tell from the UI whether a
region was never detected (an OCR/layout threshold problem) or detected and
then discarded (a postprocessing problem) -- two very different fixes.

So this module hooks LayoutPostprocessor.__init__, which receives the raw
clusters as its argument, and stashes a plain-dict snapshot keyed by the Page
it belongs to. The hook is read-only: it copies scalars out and hands the
original list straight through, so pipeline behaviour is unchanged.

Keying: Page is weakref-able but NOT hashable, so a WeakKeyDictionary is not an
option. Entries are keyed by id(page) and removed by a weakref.finalize
callback when that Page is collected -- without which a later object could
reuse the address and silently inherit another page's clusters.
"""

import threading
import weakref
from typing import Any, Dict, List, Optional

from idp.core.logging import logger

# id(Page) -> list of raw cluster snapshots. Entries are dropped by the
# finalizer registered in _remember() as soon as the Page is collected, so this
# never grows across documents.
_RAW_CLUSTERS: Dict[int, List[Dict[str, Any]]] = {}

_INSTALL_LOCK = threading.Lock()
_installed = False


def _top_left_bbox(bbox: Any, page_height: float) -> List[float]:
    """Mirror of parser._extract_top_left_bbox, kept local to avoid a circular import.

    to_top_left_origin() is a no-op on a bbox that already declares a top-left
    origin, so this is safe to apply unconditionally.
    """
    if hasattr(bbox, "to_top_left_origin"):
        b = bbox.to_top_left_origin(page_height)
        return [float(b.l), float(b.t), float(b.r), float(b.b)]
    return [float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b)]


def _snapshot(cluster: Any, page_height: float) -> Optional[Dict[str, Any]]:
    """Copy the scalar fields off a Cluster. Returns None if it is unreadable."""
    try:
        return {
            "id": getattr(cluster, "id", None),
            # Cluster labels are enum members; str() would render "DocItemLabel.TEXT".
            "label": getattr(getattr(cluster, "label", None), "value", None)
            or str(getattr(cluster, "label", "")),
            "confidence": float(getattr(cluster, "confidence", 0.0) or 0.0),
            "bbox": _top_left_bbox(cluster.bbox, page_height),
            "cell_count": len(getattr(cluster, "cells", []) or []),
        }
    except (AttributeError, TypeError, ValueError):
        return None


def _remember(page: Any, clusters: Any) -> None:
    key = id(page)
    page_height = float(getattr(getattr(page, "size", None), "height", 842.0) or 842.0)
    snapshots = [
        s for s in (_snapshot(c, page_height) for c in (clusters or [])) if s is not None
    ]
    _RAW_CLUSTERS[key] = snapshots
    try:
        # Drop the entry when the Page dies, so a recycled id() can never hand
        # a future page someone else's clusters.
        weakref.finalize(page, _RAW_CLUSTERS.pop, key, None)
    except TypeError:
        # Page is not weakref-able on this Docling build: fall back to leaving
        # the entry, which get_raw_clusters() consumes on read.
        pass


def install() -> bool:
    """Install the capture hook. Idempotent; safe to call on every parse.

    Returns:
        True if raw-cluster capture is active, False if this Docling build does
        not expose LayoutPostprocessor in the expected shape (in which case the
        debug overlay simply shows no raw regions).
    """
    global _installed
    with _INSTALL_LOCK:
        if _installed:
            return True
        try:
            from docling.utils.layout_postprocessor import LayoutPostprocessor
        except ImportError as exc:
            logger.warning(
                "[LayoutCapture] Docling's LayoutPostprocessor is unavailable (%s); "
                "raw layout regions will not be captured.", exc
            )
            return False

        original_init = LayoutPostprocessor.__init__

        def capturing_init(self, page, clusters, *args, **kwargs):  # type: ignore[no-untyped-def]
            try:
                _remember(page, clusters)
            except Exception as exc:  # pragma: no cover - never break a conversion
                logger.debug("[LayoutCapture] Skipped raw cluster capture: %s", exc)
            return original_init(self, page, clusters, *args, **kwargs)

        capturing_init._idp_layout_capture = True  # type: ignore[attr-defined]
        LayoutPostprocessor.__init__ = capturing_init  # type: ignore[method-assign]
        _installed = True
        logger.info("[LayoutCapture] Raw layout cluster capture installed.")
        return True


def get_raw_clusters(page: Any) -> List[Dict[str, Any]]:
    """Return the raw layout clusters captured for this page (empty if none)."""
    return _RAW_CLUSTERS.get(id(page), [])


def clear() -> None:
    """Drop all captured clusters. For tests."""
    _RAW_CLUSTERS.clear()


# Public alias: the parser reuses this to snapshot the POST-processed clusters in
# exactly the same shape as the raw ones, so the overlay can diff the two stages.
snapshot_cluster = _snapshot
