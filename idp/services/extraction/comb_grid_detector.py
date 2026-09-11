"""
Printed comb-box field recovery from the page image.

Indian KYC / loan-application forms print a row of bordered single-character
cells (a "comb" grid) for dates, application numbers, PAN, GSTIN, account
numbers, etc. Docling's OCR + reading-order assembly welds that whole row --
label and every hand-written cell -- into a SINGLE line element with one
bounding box, so no per-character geometry ever reaches ``CombBoxDetector``
and the field never gets its own box.

This module recovers the geometry the OCR threw away by looking at the page
image directly:

  1. crop the row region,
  2. binarise (adaptive threshold -- the forms are phone photos with uneven
     lighting) and strip the printed rules,
  3. split into horizontal ink bands (one per printed sub-row),
  4. connected-component the hand-written glyphs in each band,
  5. reuse ``CombBoxDetector``'s uniform-sequence finder to isolate the
     evenly-spaced run of cells from the surrounding label text,
  6. reconcile the cell count with the OCR'd value length (merge over-split
     strokes) and assign the value characters left-to-right,
  7. emit one single-character ``LayoutElement`` per cell in the PDF-point
     coordinate space Docling elements use.

The normal serializer + ``CombBoxDetector`` path then merges them into an
accurately-located field token.

Everything here is best-effort and side-effect free: missing OpenCV, an
unusable crop, or a row whose handwriting is too irregular to segment simply
yields ``[]`` and the fused row element is left untouched.
"""

from __future__ import annotations

import io
import re
from typing import List, Optional, Tuple

from idp.models.layout import LayoutElement, ElementType
from idp.services.extraction.comb_box_detector import CombBoxDetector
from idp.core.logging import logger, format_doc_log


# A value run embedded in a fused row: >= 4 uppercase/digit chars. The caller
# additionally requires >= 2 digits so plain all-caps words are excluded.
_VALUE_RUN_RE = re.compile(r"[A-Z0-9]{4,}")

_MIN_ROW_ASPECT = 6.0
_MIN_ROW_TEXT_LEN = 8
_MIN_CELLS = 4

# High-precision acceptance: only emit a recovered field box when the isolated
# cell run matches the OCR'd value length AND is geometrically clean. A phone
# photo of hand-written cells is easy to segment wrongly, so recall is
# deliberately traded for precision -- an unrecovered row still keeps its
# (coarser but correct) retained fused-row element.
_ACCEPT_GAP_CV = 0.28
_ACCEPT_WIDTH_CV = 0.45


class CombGridDetector:
    """Recovers per-cell geometry for comb-box rows Docling welded into one element."""

    # ------------------------------------------------------------------ #
    # Row-signature predicate + value extraction                         #
    # ------------------------------------------------------------------ #
    @staticmethod
    def is_fused_comb_row(text: Optional[str], bbox: Optional[List[float]]) -> bool:
        """
        True if a Docling line element looks like a welded comb-box row: a wide,
        short band pairing a human-readable label with an embedded value run
        carrying >= 2 digits (date / application no. / GSTIN / account no.).
        """
        if not text or not bbox or len(bbox) < 4:
            return False
        stripped = text.strip()
        if len(stripped) < _MIN_ROW_TEXT_LEN:
            return False
        if not re.search(r"[a-z:]", stripped):
            return False
        try:
            w = abs(float(bbox[2]) - float(bbox[0]))
            h = abs(float(bbox[3]) - float(bbox[1]))
        except (TypeError, ValueError):
            return False
        if w <= 0 or h <= 0 or (w / h) < _MIN_ROW_ASPECT:
            return False
        return len(CombGridDetector.value_runs(stripped)) > 0

    @staticmethod
    def value_runs(text: str) -> List[str]:
        """Ordered value runs inside a fused row (>= 4 chars, >= 2 digits)."""
        stripped = (text or "").strip()
        return [
            r for r in _VALUE_RUN_RE.findall(stripped)
            if r != stripped and sum(c.isdigit() for c in r) >= 2
        ]

    # ------------------------------------------------------------------ #
    # CV: crop -> ink bands -> glyph boxes (all in crop pixels)          #
    # ------------------------------------------------------------------ #
    @staticmethod
    def segment_glyph_bands(gray: "np.ndarray") -> List[List[Tuple[float, float, float, float]]]:  # noqa: F821
        """
        Segment a grayscale row crop into horizontal ink bands, and each band
        into hand-written glyph bounding boxes ``(x0, y0, x1, y1)`` in crop
        pixels. Printed rules and over-wide label blobs are filtered out.
        """
        try:
            import cv2
            import numpy as np
        except Exception:
            return []
        if gray is None or getattr(gray, "ndim", 0) != 2:
            return []
        h, w = gray.shape[:2]
        if h < 12 or w < 40:
            return []

        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        block = max(15, (h // 2) | 1)
        th = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, block, 12,
        )

        # Strip long printed rules before segmenting. The kernels are sized so
        # only near-full-extent rules are removed -- a hand-written glyph, even a
        # tall one, is well short of the crop's height/width and survives.
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, int(w * 0.45)), 1))
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, int(h * 0.82))))
        rules = cv2.add(
            cv2.morphologyEx(th, cv2.MORPH_OPEN, h_kernel),
            cv2.morphologyEx(th, cv2.MORPH_OPEN, v_kernel),
        )
        ink = cv2.subtract(th, rules)
        ink = cv2.morphologyEx(
            ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        )

        row_ink = ink.sum(axis=1) / 255.0
        band_thr = max(4.0, 0.05 * w)
        bands: List[Tuple[int, int]] = []
        start: Optional[int] = None
        for y in range(h + 1):
            on = y < h and row_ink[y] > band_thr
            if on and start is None:
                start = y
            elif not on and start is not None:
                if y - start >= 8:
                    bands.append((start, y))
                start = None

        out: List[List[Tuple[float, float, float, float]]] = []
        for (by0, by1) in bands:
            sub = ink[by0:by1]
            bh = by1 - by0
            n, _lab, stats, _cent = cv2.connectedComponentsWithStats(sub, 8)
            comps: List[Tuple[int, int, int, int]] = []
            for i in range(1, n):
                x, y, cw, ch, area = stats[i]
                if ch < 0.30 * bh or ch > 1.6 * bh:
                    continue
                if cw < 2 or cw > 0.14 * w:            # drop merged label blobs / rules
                    continue
                if area < 0.015 * bh * bh:
                    continue
                comps.append((x, y, cw, ch))
            if len(comps) < _MIN_CELLS:
                continue
            comps.sort(key=lambda c: c[0])
            out.append([
                (float(x), float(by0 + y), float(x + cw), float(by0 + y + ch))
                for (x, y, cw, ch) in comps
            ])
        return out

    # ------------------------------------------------------------------ #
    # High level: fused row element -> per-cell LayoutElements           #
    # ------------------------------------------------------------------ #
    def recover_cell_elements(
        self,
        image_bytes: bytes,
        elem: LayoutElement,
        page_px_w: float,
        page_px_h: float,
        pdf_w: float,
        pdf_h: float,
        doc_id: str = "DOC",
    ) -> List[LayoutElement]:
        """
        Crop the row region for ``elem``, segment the hand-written glyphs,
        isolate the evenly-spaced comb run, and -- only when it matches the
        OCR'd value length and is geometrically clean -- return ONE
        ``comb_box_merged`` field element (union of the cell run, value text)
        in PDF-point (top-left origin) coordinates. Otherwise return ``[]`` and
        the caller keeps the retained fused-row element.
        """
        runs = self.value_runs(elem.text or "")
        if not runs or not image_bytes:
            return []
        if page_px_w <= 0 or page_px_h <= 0 or pdf_w <= 0 or pdf_h <= 0:
            return []
        try:
            import numpy as np
            from PIL import Image
        except Exception:
            return []

        sx, sy = page_px_w / pdf_w, page_px_h / pdf_h
        ex0, ey0 = elem.bbox[0] * sx, elem.bbox[1] * sy
        ex1, ey1 = elem.bbox[2] * sx, elem.bbox[3] * sy
        pad_x = (ex1 - ex0) * 0.02
        pad_y = (ey1 - ey0) * 0.60
        cl = max(0, int(ex0 - pad_x))
        ct = max(0, int(ey0 - pad_y))
        cr = min(int(page_px_w), int(ex1 + pad_x))
        cb = min(int(page_px_h), int(ey1 + pad_y))
        if cr - cl < 24 or cb - ct < 10:
            return []

        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("L")
            crop = np.asarray(img.crop((cl, ct, cr, cb)))
        except Exception as ex:
            logger.warning(format_doc_log(doc_id, f"comb-grid crop failed ({elem.id}): {ex}"))
            return []

        bands = self.segment_glyph_bands(crop)
        if not bands:
            return []

        seq_finder = CombBoxDetector(min_sequence_length=_MIN_CELLS)
        out: List[LayoutElement] = []

        # Pair value runs with ink bands top-to-bottom.
        for bi, run in enumerate(runs):
            if bi >= len(bands):
                break
            cells = self._isolate_comb_run(bands[bi], len(run), seq_finder)
            if len(cells) != len(run) or len(cells) < _MIN_CELLS:
                continue
            if not self._run_is_clean(cells):
                continue

            fx0 = (cl + min(c[0] for c in cells)) / sx
            fx1 = (cl + max(c[2] for c in cells)) / sx
            fy0 = (ct + min(c[1] for c in cells)) / sy
            fy1 = (ct + max(c[3] for c in cells)) / sy
            if fx1 - fx0 <= 0 or fy1 - fy0 <= 0:
                continue

            out.append(
                LayoutElement(
                    id=f"combgrid-{elem.page_number}-{elem.id}-{bi}",
                    type=ElementType.TEXT,
                    text=run,
                    bbox=[fx0, fy0, fx1, fy1],
                    confidence=0.6,
                    page_number=elem.page_number,
                    reading_order=(elem.reading_order or 0),
                    source="comb_box_merged",
                    structure_source="comb_grid",
                    ocr_original=elem.text,
                    metadata={"recovery": "comb_grid", "needs_vlm": True,
                              "num_cells": len(cells)},
                )
            )
            logger.info(format_doc_log(
                doc_id, f"comb-grid: {elem.id} recovered field {run!r} "
                        f"from {len(cells)} cells"
            ))
        return out

    @staticmethod
    def _run_is_clean(cells: List[Tuple[float, float, float, float]]) -> bool:
        """Even cell pitch and comparable cell widths -> a real comb run."""
        try:
            import statistics
        except Exception:
            return False
        if len(cells) < _MIN_CELLS:
            return False
        centres = [(c[0] + c[2]) / 2.0 for c in cells]
        pitches = [centres[i + 1] - centres[i] for i in range(len(centres) - 1)]
        widths = [c[2] - c[0] for c in cells]
        if min(pitches) <= 0 or statistics.mean(pitches) <= 0 or statistics.mean(widths) <= 0:
            return False
        gap_cv = statistics.pstdev(pitches) / statistics.mean(pitches)
        width_cv = statistics.pstdev(widths) / statistics.mean(widths)
        return gap_cv <= _ACCEPT_GAP_CV and width_cv <= _ACCEPT_WIDTH_CV

    @staticmethod
    def _isolate_comb_run(
        glyphs: List[Tuple[float, float, float, float]],
        target_count: int,
        seq_finder: CombBoxDetector,
    ) -> List[Tuple[float, float, float, float]]:
        """
        From all glyph boxes in a band, return just the evenly-spaced comb-cell
        run (dropping surrounding label glyphs), reconciled toward
        ``target_count`` by merging the closest over-split strokes.
        """
        if len(glyphs) < _MIN_CELLS:
            return []

        elems = [
            LayoutElement(
                id=f"g{i}", text="0", bbox=list(g), page_number=1,
                type=ElementType.TEXT, confidence=0.5,
                source="cv", structure_source="cv", reading_order=i,
            )
            for i, g in enumerate(glyphs)
        ]
        sequences = seq_finder._find_comb_box_sequences(elems)
        if not sequences:
            return []
        sequences.sort(key=lambda s: (abs(len(s) - target_count), -len(s)))
        candidate = sorted((tuple(e.bbox) for e in sequences[0]), key=lambda b: b[0])

        # Merge the closest-adjacent pair repeatedly while over-segmented
        # (hand-written strokes that broke into two components).
        while len(candidate) > target_count >= _MIN_CELLS:
            gaps = [
                (candidate[i + 1][0] - candidate[i][2], i)
                for i in range(len(candidate) - 1)
            ]
            _, j = min(gaps, key=lambda t: t[0])
            a, b = candidate[j], candidate[j + 1]
            merged = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
            candidate = candidate[:j] + [merged] + candidate[j + 2:]

        return candidate
