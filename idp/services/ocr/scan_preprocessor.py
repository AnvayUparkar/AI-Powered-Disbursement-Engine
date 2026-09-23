"""
Pixel-level pre-cleaning for scanned documents before Docling ingestion.

Single responsibility: given a raw file path known to be scanned, return a
*new* file path with cleaned pixels — same page count/order, same format
family.  Never mutates the original.

Supported input types
---------------------
- PDF  (``file_category == "pdf"``)  → multi-page; output is a fresh PDF
  whose pages are the preprocessed raster images re-embedded at the original
  point dimensions, so ``parser.py``'s page-dimension logic is unaffected.
- Image (``file_category == "image"``) → single page; output is a PNG/ext
  written to output_dir.

Call-site override
------------------
After calling this function, pass ``images_scale=1.0`` to the DoclingOptions
instance used for *this call only*.  The output image is already at the target
resolution (rasterised at ``target_scale``), so re-scaling it a second time
inside Docling merely upsamples an already-raster image for no benefit.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple,Optional

from pydantic import BaseModel

from idp.core.logging import logger, format_doc_log
from idp.services.ocr.preprocessing import OCRImagePreprocessor


class ScanPreprocessingResult(BaseModel):
    """Outcome of ``preprocess_scanned_document``."""

    processed_path: str
    original_path: str
    pages_processed: int
    per_page_metadata: List[Dict[str, Any]]
    deskewed_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _preprocess_page(
    page_index: int,
    page_bytes: bytes,
    doc_id: str,
    preprocessor: OCRImagePreprocessor,
) -> Tuple[int, bytes, Dict[str, Any]]:
    """Process a single rasterised page; returns (page_index, cleaned_bytes, metadata)."""
    cleaned_bytes, meta = preprocessor.preprocess_image(
        page_bytes, doc_id=f"{doc_id}_p{page_index + 1}"
    )
    return page_index, cleaned_bytes, meta


def _process_pdf(
    file_path: str,
    target_scale: float,
    doc_id: str,
    output_path: str,
    preprocessor: OCRImagePreprocessor,
    deskewed_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Rasterise every page of *file_path* at *target_scale*, run each page
    through OCRImagePreprocessor, and write the cleaned pages as a new PDF
    to *output_path*.  Page point-dimensions are preserved so that
    ``parser.py``'s ``pages_dimensions`` logic is unaffected.

    Returns per-page metadata dicts from OCRImagePreprocessor.
    """
    import fitz  # PyMuPDF — already a dependency of document_preprocessor.py

    src_doc = fitz.open(file_path)
    n_pages = len(src_doc)

    # Rasterise all pages first so we can parallelise the CPU-bound cleanup.
    # Store (page_index, raw_png_bytes, original_point_rect).
    raw_pages: List[Tuple[int, bytes, Any]] = []
    for idx, page in enumerate(src_doc):
        matrix = fitz.Matrix(target_scale, target_scale)
        pix = page.get_pixmap(matrix=matrix)
        raw_pages.append((idx, pix.tobytes("png"), page.rect))
    src_doc.close()

    # Parallelise preprocessing across pages.
    results: List[Any] = [None] * n_pages
    with ThreadPoolExecutor(max_workers=min(4, n_pages)) as pool:
        futures = {
            pool.submit(_preprocess_page, idx, raw_bytes, doc_id, preprocessor): idx
            for idx, raw_bytes, _ in raw_pages
        }
        for fut in as_completed(futures):
            page_index, cleaned_bytes, meta = fut.result()
            results[page_index] = (page_index, cleaned_bytes, meta)

    # Reassemble into a new PDF, preserving original point dimensions.
    out_doc = fitz.open()
    deskewed_doc = fitz.open() if deskewed_path else None
    per_page_metadata: List[Dict[str, Any]] = []
    for idx, entry in enumerate(results):
        _, cleaned_bytes, meta = entry
        orig_rect = raw_pages[idx][2]  # page.rect (point coordinates)
        new_page = out_doc.new_page(width=orig_rect.width, height=orig_rect.height)
        new_page.insert_image(orig_rect, stream=cleaned_bytes)
        if deskewed_doc is not None:
            d_bytes = meta.get("deskewed_bytes") or cleaned_bytes
            d_page = deskewed_doc.new_page(width=orig_rect.width, height=orig_rect.height)
            d_page.insert_image(orig_rect, stream=d_bytes)
        per_page_metadata.append(meta)
        logger.debug(
            format_doc_log(
                doc_id,
                f"Page {idx + 1}/{n_pages} preprocessed: "
                f"rotation={meta.get('rotation_angle', 0):.1f}deg "
                f"contrast_enhanced={meta.get('contrast_enhanced')} "
                f"denoised={meta.get('denoised')} "
                f"binarized={meta.get('binarized')}",
            )
        )

    out_doc.save(output_path, garbage=4, deflate=True)
    out_doc.close()
    if deskewed_doc is not None:
        deskewed_doc.save(deskewed_path, garbage=4, deflate=True)
        deskewed_doc.close()
    return per_page_metadata


def _process_image(
    file_path: str,
    doc_id: str,
    output_path: str,
    preprocessor: OCRImagePreprocessor,
    deskewed_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Run the single image through OCRImagePreprocessor and write the result
    to *output_path*.

    Returns a single-element list of per-page metadata.
    """
    with open(file_path, "rb") as fh:
        raw_bytes = fh.read()

    cleaned_bytes, meta = preprocessor.preprocess_image(raw_bytes, doc_id=doc_id)

    with open(output_path, "wb") as fh:
        fh.write(cleaned_bytes)

    if deskewed_path:
        d_bytes = meta.get("deskewed_bytes") or cleaned_bytes
        with open(deskewed_path, "wb") as fh:
            fh.write(d_bytes)

    return [meta]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def preprocess_scanned_document(
    file_path: str,
    file_category: str,
    target_scale: float,
    doc_id: str,
    output_dir: str,
    return_stage: Optional[str] = None,
) -> ScanPreprocessingResult:
    """
    Pre-clean a scanned document at the pixel level before Docling ingestion.

    Parameters
    ----------
    file_path:
        Absolute path to the raw (original) file downloaded from S3.
    file_category:
        ``"pdf"`` or ``"image"`` — taken from ``PreprocessedDocument.file_category``.
    target_scale:
        Rasterisation scale factor applied to PDF pages (pass
        ``SCANNED_DOCUMENTS_PROFILE.images_scale``, currently ``3.0``).
        Ignored for image inputs (they are already raster).
    doc_id:
        Document identifier forwarded to logging and OCRImagePreprocessor.
    output_dir:
        Directory where the cleaned output file will be written.
        Must already exist (``create_temp_dir`` in document_processor.py
        ensures this).
    return_stage:
        Optional stage to return as primary ``processed_path`` (e.g. ``"deskewed"``).

    Returns
    -------
    ScanPreprocessingResult
        ``processed_path`` points to the cleaned file that should be passed
        to ``docling_parser.parse()``.  ``original_path`` is ``file_path``
        unchanged.  ``deskewed_path`` points to the deskewed-only version.

    Raises
    ------
    ValueError
        If *file_category* is neither ``"pdf"`` nor ``"image"``.
    OSError
        If *file_path* does not exist or *output_dir* is not writable.
    """
    if not os.path.exists(file_path):
        raise OSError(f"[{doc_id}] Source file not found: {file_path}")

    if file_category not in ("pdf", "image"):
        raise ValueError(
            f"[{doc_id}] preprocess_scanned_document only handles 'pdf' or 'image', "
            f"got: {file_category!r}"
        )

    preprocessor = OCRImagePreprocessor()

    if file_category == "pdf":
        output_path = os.path.join(output_dir, f"{doc_id}_preprocessed.pdf")
        deskewed_path = os.path.join(output_dir, f"{doc_id}_deskewed.pdf")
        logger.info(
            format_doc_log(
                doc_id,
                f"Starting pixel-level scan preprocessing (PDF) -> {output_path}",
            )
        )
        per_page_metadata = _process_pdf(
            file_path=file_path,
            target_scale=target_scale,
            doc_id=doc_id,
            output_path=output_path,
            preprocessor=preprocessor,
            deskewed_path=deskewed_path,
        )

    else:  # image
        ext = os.path.splitext(file_path)[1] or ".png"
        output_path = os.path.join(output_dir, f"{doc_id}_preprocessed{ext}")
        deskewed_path = os.path.join(output_dir, f"{doc_id}_deskewed{ext}")
        logger.info(
            format_doc_log(
                doc_id,
                f"Starting pixel-level scan preprocessing (image) -> {output_path}",
            )
        )
        per_page_metadata = _process_image(
            file_path=file_path,
            doc_id=doc_id,
            output_path=output_path,
            preprocessor=preprocessor,
            deskewed_path=deskewed_path,
        )

    logger.info(
        format_doc_log(
            doc_id,
            f"Scan preprocessing complete: {len(per_page_metadata)} page(s) cleaned",
        )
    )

    primary_path = deskewed_path if return_stage == "deskewed" else output_path

    return ScanPreprocessingResult(
        processed_path=primary_path,
        original_path=file_path,
        pages_processed=len(per_page_metadata),
        per_page_metadata=per_page_metadata,
        deskewed_path=deskewed_path,
    )
