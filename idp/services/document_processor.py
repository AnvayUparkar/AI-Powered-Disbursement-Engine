import os
import time
import json
import asyncio
from typing import Optional, Dict, List, Tuple, Any
from idp.services.storage.s3 import S3Storage
from idp.services.document_preprocessor import DocumentPreprocessor, PreprocessedDocument
from idp.services.docling.parser import DoclingParser, DoclingParseResult
from idp.services.docling.options import DoclingOptions
from config.doc_types import get_canonical_doc_type
from config.docling_profiles import get_profile_for_document_type
from idp.models.ocr import OCRResult, OCRElement
from idp.models.layout import LayoutElement
from idp.services.vlm.router import ConfidenceRouter
from idp.services.vlm.client import VLMClient, VLMResult
from idp.services.output.serializer import DocumentSerializer
from idp.models.document import ParsedDocument
from idp.models.processing import ProcessingMetrics
from idp.utils.file_utils import create_temp_dir, cleanup_temp_dir
from idp.utils.image_utils import crop_image_region
from idp.core.config import settings
from config.tenant import SAFE_ID_PATTERN, current_tenant_id
from idp.core.logging import logger, format_doc_log
from idp.utils.timing import StageClock, format_timing_table
from idp.services.ocr.scan_preprocessor import preprocess_scanned_document


class DocumentProcessor:
    """Core Node 2 Document Processing Pipeline Orchestrator."""

    def __init__(self):
        self.storage = S3Storage()
        self.preprocessor = DocumentPreprocessor()
        # One DoclingParser per document-type profile (config/docling_profiles.py),
        # built lazily and cached so each profile's DocumentConverter is warmed up
        # exactly once (see DoclingPipeline -> get_cached_converter, which itself
        # caches by options fingerprint) rather than rebuilt every time the
        # document-type mix changes mid-batch.
        self._docling_parsers: Dict[str, DoclingParser] = {}
        self.router = ConfidenceRouter()
        self.vlm_client = VLMClient()
        self.serializer = DocumentSerializer()
        self._in_flight_tasks: Dict[str, asyncio.Task] = {}
        self._redis_client = None

    def _get_docling_parser(self, doc_type: str, is_scanned: Optional[bool] = None) -> DoclingParser:
        """Return the cached DoclingParser tuned for this canonical document type.

        `is_scanned` is the preprocessor's content-based text-layer inspection
        result (PreprocessedDocument.is_scanned_pdf) and takes precedence over
        the doc_type-only heuristic inside get_profile_for_document_type --
        see that function's docstring. It is folded into the cache key since
        the same doc_type can now resolve to different profiles.
        """
        cache_key = f"{doc_type}::{is_scanned}"
        parser = self._docling_parsers.get(cache_key)
        if parser is None:
            profile: DoclingOptions = get_profile_for_document_type(doc_type, is_scanned=is_scanned)
            parser = DoclingParser(profile)
            self._docling_parsers[cache_key] = parser
        return parser

    async def _get_redis_client(self):
        """Lazily initialize and return Redis async client, or None if unavailable."""
        if not hasattr(self, "_redis_client") or self._redis_client is None:
            try:
                import redis.asyncio as aioredis
                client = aioredis.from_url(
                    getattr(settings, "REDIS_URL", "redis://127.0.0.1:6379/0"),
                    socket_connect_timeout=1.0,
                    socket_timeout=2.0
                )
                await client.ping()
                self._redis_client = client
            except Exception as e:
                logger.debug(f"Redis is unavailable for distributed locking: {e}")
                self._redis_client = None
        return self._redis_client

    async def _poll_existing_job(
        self,
        document_id: str,
        bucket: str,
        max_timeout: int = getattr(settings, "REDIS_LOCK_TIMEOUT_SECONDS", 900),
        poll_interval: float = 1.0
    ) -> Dict[str, Any]:
        """Poll Redis and S3 for completion of an in-flight document processing task."""
        start_poll = time.time()
        redis_client = await self._get_redis_client()
        result_key = f"result:idp:process:{document_id}"
        lock_key = f"lock:idp:process:{document_id}"

        while time.time() - start_poll < max_timeout:
            # 1. Check Redis for completed result payload
            if redis_client:
                try:
                    cached_bytes = await redis_client.get(result_key)
                    if cached_bytes:
                        logger.info(format_doc_log(document_id, f"In-flight task completed; retrieved result from Redis after {time.time() - start_poll:.1f}s"))
                        return json.loads(cached_bytes)
                except Exception as r_err:
                    logger.debug(format_doc_log(document_id, f"Redis poll note: {r_err}"))

            # 2. Check S3 storage for completed parsed document
            try:
                parsed_s3_key = f"{settings.PARSED_DOCUMENT_PREFIX}{document_id}.json"
                if await self.storage.exists(parsed_s3_key, bucket=bucket):
                    temp_res_dir = create_temp_dir(prefix=f"poll_{document_id}_")
                    dest_file = os.path.join(temp_res_dir, f"{document_id}.json")
                    try:
                        await self.storage.download(key=parsed_s3_key, dest_path=dest_file, bucket=bucket, doc_id=document_id)
                        with open(dest_file, "r", encoding="utf-8") as f:
                            parsed_data = json.load(f)
                        logger.info(format_doc_log(document_id, f"In-flight task completed; retrieved parsed document from S3 after {time.time() - start_poll:.1f}s"))
                        return {
                            "document_id": document_id,
                            "status": "completed",
                            "output_location": f"s3://{bucket}/{parsed_s3_key}",
                            "processing_time_seconds": round(time.time() - start_poll, 3),
                            "raw_text": parsed_data.get("text") or parsed_data.get("raw_text", ""),
                            "formatted_text": parsed_data.get("formatted_text", ""),
                            "extracted_fields": (parsed_data.get("custom_metadata") or {}).get("llm_extracted_fields", {}),
                            "field_locations": (parsed_data.get("custom_metadata") or {}).get("field_locations", {}),
                            "ocr_tokens": (parsed_data.get("custom_metadata") or {}).get("ocr_tokens", []),
                        }
                    finally:
                        cleanup_temp_dir(temp_res_dir)
            except Exception as s3_err:
                logger.debug(format_doc_log(document_id, f"S3 poll note: {s3_err}"))

            # 3. Check if lock was released without storing result (worker crashed or failed)
            if redis_client:
                try:
                    still_locked = await redis_client.exists(lock_key)
                    if not still_locked:
                        logger.warning(format_doc_log(document_id, "In-flight Redis lock released without result. Retrying processing directly."))
                        break
                except Exception:
                    pass

            await asyncio.sleep(poll_interval)

        # Fallback: if polling timed out or lock was dropped, run processing directly
        return await self._process_document_internal(document_id, s3_key="", s3_bucket=bucket)

    async def process_document(
        self,
        document_id: str,
        s3_key: str,
        s3_bucket: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes end-to-end processing lifecycle for a single document with in-flight deduplication
        and Redis distributed mutex locking (Singleflight Pattern).
        """
        # Level 1: In-process task deduplication (same event loop)
        if document_id in self._in_flight_tasks:
            logger.info(format_doc_log(document_id, "Document is already actively being processed by in-flight task. Awaiting existing task..."))
            return await asyncio.shield(self._in_flight_tasks[document_id])

        # Level 2: Distributed Redis lock (across multi-process workers)
        redis_client = await self._get_redis_client()
        lock_key = f"lock:idp:process:{document_id}"
        lock_acquired = False

        if redis_client:
            try:
                lock_acquired = bool(await redis_client.set(
                    lock_key, "processing", nx=True, ex=getattr(settings, "REDIS_LOCK_TIMEOUT_SECONDS", 900)
                ))
                if not lock_acquired:
                    logger.info(format_doc_log(document_id, "Another worker holds Redis distributed lock for this document. Polling for completion..."))
                    bucket = s3_bucket if (isinstance(s3_bucket, str) and s3_bucket.strip()) else settings.S3_BUCKET
                    return await self._poll_existing_job(document_id, bucket)
            except Exception as r_err:
                logger.debug(format_doc_log(document_id, f"Redis lock check note: {r_err}"))

        loop = asyncio.get_running_loop()
        task = loop.create_task(self._process_document_internal(document_id, s3_key, s3_bucket, redis_client=redis_client))
        self._in_flight_tasks[document_id] = task

        try:
            return await task
        finally:
            self._in_flight_tasks.pop(document_id, None)
            if redis_client and lock_acquired:
                try:
                    await redis_client.delete(lock_key)
                except Exception:
                    pass

    async def _process_document_internal(
        self,
        document_id: str,
        s3_key: str,
        s3_bucket: Optional[str] = None,
        redis_client: Any = None
    ) -> Dict[str, Any]:
        """
        Internal implementation of end-to-end processing lifecycle for a single document.

        Returns:
            Dict containing document_id, status, output_location, and processing_time.
        """
        start_time = time.time()
        clock = StageClock()
        bucket = s3_bucket if (isinstance(s3_bucket, str) and s3_bucket.strip()) else settings.S3_BUCKET
        temp_dir = create_temp_dir(prefix=f"node2_{document_id}_")

        logger.info(format_doc_log(document_id, f"Beginning Node 2 processing for s3://{bucket}/{s3_key}"))

        try:
            # Step 1: Download raw document from S3
            filename = os.path.basename(s3_key)
            doc_type_hint = get_canonical_doc_type(filename)
            local_file_path = os.path.join(temp_dir, filename)
            await self.storage.download(key=s3_key, dest_path=local_file_path, bucket=bucket, doc_id=document_id)
            clock.lap("download")

            # Step 2: Preprocess and validate document
            prep_doc: PreprocessedDocument = await asyncio.to_thread(
                self.preprocessor.preprocess, local_file_path, doc_id=document_id
            )
            clock.lap("preprocess")

            metrics = ProcessingMetrics()

            # Fast path for XML documents
            if prep_doc.file_category == "xml":
                parsed_doc = await asyncio.to_thread(
                    self.serializer.parse_xml_fast_path,
                    file_path=local_file_path,
                    doc_id=document_id,
                    s3_bucket=bucket,
                    s3_key=s3_key
                )
                clock.lap("parse")
                self._stamp_stage_timings(parsed_doc, clock)
                output_location = await self._save_and_upload_output(parsed_doc, document_id, bucket)
                clock.lap("save")
                elapsed = time.time() - start_time
                self._log_timing_summary(document_id, clock, elapsed)
                return {
                    "document_id": document_id,
                    "status": "completed",
                    "output_location": output_location,
                    "processing_time_seconds": round(elapsed, 3),
                    "raw_text": parsed_doc.text,
                    "formatted_text": parsed_doc.formatted_text or "",
                    "extracted_fields": (parsed_doc.custom_metadata or {}).get("llm_extracted_fields", {}),
                }

            # Fast path for Loan Agreement PDFs (digital signature inspection via pyHanko)
            from pipeline.engines.pyhanko_inspector import is_loan_agreement
            if (is_loan_agreement(filename) or is_loan_agreement(doc_type_hint)) and local_file_path.lower().endswith(".pdf"):
                parsed_doc = await asyncio.to_thread(
                    self.serializer.parse_loan_agreement_fast_path,
                    file_path=local_file_path,
                    doc_id=document_id,
                    filename=filename,
                    s3_bucket=bucket,
                    s3_key=s3_key,
                )
                clock.lap("parse")
                self._stamp_stage_timings(parsed_doc, clock)
                output_location = await self._save_and_upload_output(parsed_doc, document_id, bucket)
                clock.lap("save")
                elapsed = time.time() - start_time
                self._log_timing_summary(document_id, clock, elapsed)
                return {
                    "document_id": document_id,
                    "status": "completed",
                    "output_location": output_location,
                    "processing_time_seconds": round(elapsed, 3),
                    "raw_text": parsed_doc.text,
                    "formatted_text": parsed_doc.formatted_text or "",
                    "extracted_fields": (parsed_doc.custom_metadata or {}).get("llm_extracted_fields", {}),
                }

            # Step 3: Docling layout + integrated OCR parsing (runs on all document types),
            # using the DoclingOptions profile tuned for this document's canonical type
            # (character-box KYC forms, scanned bank statements, digital PDFs, etc.)

            # Step 3a: For scanned documents, apply pixel-level cleanup (deskew, CLAHE,
            # denoising, adaptive binarisation) before Docling ingestion.  The digital PDF
            # path is never entered here — the gate is prep_doc.is_scanned_pdf which is set
            # only when the text-layer inspection in DocumentPreprocessor detects < 50 chars/page.
            docling_input_path = local_file_path  # default: pass raw file unchanged
            # Build the primary parser for this document type.  For scanned docs we may
            # swap it below to one using images_scale=1.0 (see note in scan branch).
            docling_profile = self._get_docling_parser(doc_type_hint, is_scanned=prep_doc.is_scanned_pdf)

            if prep_doc.is_scanned_pdf and settings.ENABLE_SCAN_PREPROCESSING:
                try:
                    # Use the actual profile that will be applied to this document so
                    # the rasterisation scale matches what Docling would have used.
                    # (e.g. application_form -> CHARACTER_BOX_FORMS_PROFILE.images_scale=2.0,
                    #  not SCANNED_DOCUMENTS_PROFILE.images_scale=3.0)
                    raster_scale = docling_profile.options.images_scale
                    scan_result = await asyncio.to_thread(
                        preprocess_scanned_document,
                        local_file_path,
                        prep_doc.file_category,
                        raster_scale,
                        document_id,
                        temp_dir,
                    )
                    docling_input_path = scan_result.processed_path
                    logger.info(format_doc_log(
                        document_id,
                        f"Scan preprocessing done: {scan_result.pages_processed} page(s) "
                        f"cleaned at scale={raster_scale}x -> {scan_result.processed_path}"
                    ))
                    # CRITICAL: the preprocessed PDF is already rasterised at raster_scale.
                    # Passing it to Docling with the same images_scale would upscale it
                    # a second time, blurring pixels and degrading OCR accuracy.
                    # Use a separate parser instance with images_scale=1.0 ("read as-is").
                    preprocessed_options = docling_profile.options.model_copy(
                        update={"images_scale": raster_scale}
                    )
                    docling_profile = DoclingParser(preprocessed_options)
                except Exception as scan_err:
                    # Non-fatal: log and fall back to the original file so the pipeline
                    # continues rather than failing the whole document.
                    logger.warning(format_doc_log(
                        document_id,
                        f"Scan preprocessing failed (non-fatal), using original file: {scan_err}"
                    ))
                    docling_input_path = local_file_path
                clock.lap("scan_cleanup")

            # ROUTING DECISION: LightOnOCR vs Docling for scanned pages
            # LightOnOCR receives the SAME preprocessed images as Docling would have
            if prep_doc.is_scanned_pdf and settings.LIGHTONOCR_ENABLED:
                # ═══════════════════════════════════════════════════════════════
                # LightOnOCR Route for Scanned Pages
                # ═══════════════════════════════════════════════════════════════
                from idp.services.ocr.lightonocr_adapter import LightOnOCRAdapter

                lightonocr_adapter = LightOnOCRAdapter()
                ocr_results: List[OCRResult] = []

                logger.info(format_doc_log(
                    document_id,
                    f"Routing {prep_doc.page_count} scanned pages to LightOnOCR via LiteLLM "
                    f"(model={settings.LIGHTONOCR_MODEL})"
                    f"{' (preprocessed)' if docling_input_path != local_file_path else ' (raw)'}"
                ))

                lightonocr_start = time.time()
                lightonocr_pages_processed = 0
                lightonocr_pages_failed = 0

                # Extract page images from the PREPROCESSED PDF (same images Docling would use)
                page_image_data_for_ocr = await self._get_page_images(docling_input_path, prep_doc)
                clock.lap("page_images")

                for page_idx, (page_bytes, img_w, img_h) in enumerate(page_image_data_for_ocr):
                    page_num = page_idx + 1

                    try:
                        # Blocking HTTP call to LiteLLM: run it off the event loop so /health and
                        # other in-flight requests on this idp pod keep being served.
                        ocr_res = await asyncio.to_thread(
                            lightonocr_adapter.process_page_to_ocr_result,
                            image_bytes=page_bytes,
                            page_number=page_num,
                            image_width=img_w,
                            image_height=img_h,
                            doc_id=document_id
                        )

                        if ocr_res and not ocr_res.extraction_failed:
                            ocr_results.append(ocr_res)
                            lightonocr_pages_processed += 1
                        else:
                            ocr_results.append(OCRResult(
                                page_number=page_num,
                                elements=[],
                                extraction_failed=True,
                                image_width=img_w,
                                image_height=img_h
                            ))
                            lightonocr_pages_failed += 1
                            logger.warning(format_doc_log(
                                document_id,
                                f"LightOnOCR failed on page {page_num} - will use VLM fallback"
                            ))

                    except Exception as e:
                        logger.error(format_doc_log(
                            document_id,
                            f"LightOnOCR exception on page {page_num}: {e}"
                        ))
                        ocr_results.append(OCRResult(
                            page_number=page_num,
                            elements=[],
                            extraction_failed=True,
                            image_width=img_w,
                            image_height=img_h
                        ))
                        lightonocr_pages_failed += 1

                clock.lap("lightonocr")
                metrics.lightonocr_processing_time = round(time.time() - lightonocr_start, 3)
                metrics.lightonocr_pages_processed = lightonocr_pages_processed
                metrics.lightonocr_pages_failed = lightonocr_pages_failed

                logger.info(format_doc_log(
                    document_id,
                    f"LightOnOCR completed: {lightonocr_pages_processed}/{prep_doc.page_count} pages, "
                    f"{lightonocr_pages_failed} failures, {metrics.lightonocr_processing_time:.2f}s"
                ))

                docling_result = None
                metrics.docling_processing_time = 0.0

            else:
                # ═══════════════════════════════════════════════════════════════
                # EXISTING: Docling Path (digital PDFs and scanned when LightOnOCR disabled)
                # ═══════════════════════════════════════════════════════════════
                if prep_doc.is_scanned_pdf:
                    logger.info(format_doc_log(
                        document_id,
                        "Scanned document but LIGHTONOCR_ENABLED is false - using Docling OCR"
                    ))
                ocr_results = []
                docling_start = time.time()
                docling_result = None
                try:
                    docling_result = await asyncio.to_thread(
                        docling_profile.parse, docling_input_path, doc_id=document_id
                    )
                except Exception as e:
                    logger.warning(format_doc_log(document_id, f"Docling parsing warning: {e}. Proceeding with fallback parsing."))
                metrics.docling_processing_time = round(time.time() - docling_start, 3)
                clock.lap("docling")
                if docling_result is not None:
                    metrics.docling_model_timings = dict(docling_result.model_timings)

            # Step 4: Capture page images for VLM region cropping and comb-grid recovery.
            # This re-rasterises every page a second time (the OCR/LightOnOCR pass above already
            # rasterised them once) -- expensive on both CPU and memory for a multi-page scanned
            # document, so skip it entirely when nothing downstream can use it: both VLM branches
            # below require settings.VLM_ENABLED, and comb-grid recovery only runs when
            # docling_result is not None (never true on the LightOnOCR route, since that route
            # leaves docling_result as None). Previously this ran unconditionally, so with
            # VLM_ENABLED=False (this deployment's default) every scanned document paid the full
            # cost of a second full-document rasterisation for zero benefit.
            # For LightOnOCR route: use the ORIGINAL file (not preprocessed) for VLM
            needs_page_images = settings.VLM_ENABLED or docling_result is not None
            if needs_page_images:
                page_image_data = await self._get_page_images(local_file_path, prep_doc)
            else:
                page_image_data = []
            page_images: List[bytes] = [item[0] for item in page_image_data]
            clock.lap("page_images")

            # Step 5: Selective VLM Fallback Routing
            vlm_start = time.time()
            vlm_corrections: Dict[str, VLMResult] = {}
            vlm_used = False

            if prep_doc.is_scanned_pdf and settings.LIGHTONOCR_ENABLED and settings.VLM_ENABLED and ocr_results:
                for ocr_res in ocr_results:
                    pno = ocr_res.page_number
                    page_bytes = page_images[pno - 1] if pno <= len(page_images) else b""

                    # Check if page needs VLM (extraction_failed or low quality elements)
                    needs_vlm_page = ocr_res.extraction_failed or ocr_res.low_confidence_count > 0

                    if needs_vlm_page and page_bytes:
                        logger.info(format_doc_log(
                            document_id,
                            f"Routing page {pno} to VLM (LightOnOCR quality insufficient)"
                        ))

                        vlm_res = await self.vlm_client.analyze_region(
                            image_bytes=page_bytes,
                            ocr_element=OCRElement(
                                id=f"lightonocr-fallback-p{pno}",
                                text="",
                                bbox=[0.0, 0.0, ocr_res.image_width, ocr_res.image_height],
                                confidence=0.0,
                                page_number=pno,
                                source="lightonocr"
                            ),
                            context_hint=f"Full page {pno} OCR fallback",
                            doc_id=document_id
                        )

                        if vlm_res:
                            vlm_elem = OCRElement(
                                id=f"vlm-p{pno}-full",
                                text=vlm_res.text,
                                bbox=[0.0, 0.0, ocr_res.image_width, ocr_res.image_height],
                                confidence=vlm_res.confidence,
                                page_number=pno,
                                source="vlm_corrected",
                                ocr_original=ocr_res.elements[0].text if ocr_res.elements else "",
                                needs_vlm=False
                            )

                            ocr_res.elements = [vlm_elem]
                            ocr_res.extraction_failed = False
                            ocr_res.average_confidence = vlm_res.confidence
                            ocr_res.low_confidence_count = 0
                            metrics.vlm_fallback_count += 1
                            vlm_used = True

                        await asyncio.sleep(0.25)

            elif docling_result and docling_result.elements and settings.VLM_ENABLED:
                flagged_elements = self.router.get_low_confidence_layout_elements(
                    docling_result.elements, doc_id=document_id
                )
                metrics.ocr_low_confidence_count = len(flagged_elements)

                for elem in flagged_elements:
                    pno = elem.page_number
                    page_bytes = page_images[pno - 1] if pno <= len(page_images) else b""
                    img_w = 595.0
                    img_h = 842.0
                    if pno <= len(docling_result.pages_dimensions):
                        img_w = docling_result.pages_dimensions[pno - 1].get("width", 595.0)
                        img_h = docling_result.pages_dimensions[pno - 1].get("height", 842.0)

                    cropped_bytes = crop_image_region(
                        image_bytes=page_bytes,
                        bbox=elem.bbox
                    )

                    vlm_res = await self.vlm_client.analyze_region(
                        image_bytes=cropped_bytes or page_bytes,
                        ocr_element=elem,
                        context_hint=f"Page {pno} element {elem.id}",
                        doc_id=document_id
                    )

                    if vlm_res and elem.id:
                        vlm_corrections[elem.id] = vlm_res
                        # Update element in-place so downstream serializers and alignment use the corrected text
                        elem.ocr_original = elem.text
                        elem.text = vlm_res.text
                        elem.confidence = max(elem.confidence, vlm_res.confidence)
                        elem.source = "vlm_corrected"
                        metrics.vlm_fallback_count += 1
                        vlm_used = True

                        # Gentle pacing between VLM fallback calls to respect API quotas
                        await asyncio.sleep(0.25)

            metrics.vlm_processing_time = round(time.time() - vlm_start, 3)
            clock.lap("vlm")

            # Step 5.5: Recover comb-box fields that Docling welded into one line
            # element by reading the printed cell-divider grid off the page image
            # and assigning the value characters back to their cells.
            if docling_result is not None:
                try:
                    n_grid = await asyncio.to_thread(
                        self._recover_comb_grids,
                        docling_result, page_image_data, document_id
                    )
                    if n_grid:
                        logger.info(format_doc_log(
                            document_id,
                            f"Recovered {n_grid} comb-grid cell elements from the page image"
                        ))
                except Exception as grid_err:
                    logger.warning(format_doc_log(
                        document_id, f"Comb-grid recovery skipped (non-fatal): {grid_err}"
                    ))

            clock.lap("comb_grid")
            metrics.total_processing_time = round(time.time() - start_time, 3)

            # Step 6: Serialize into Canonical Unified Document Representation
            parsed_doc = await asyncio.to_thread(
                self.serializer.build_unified_document,
                doc_id=document_id,
                filename=filename,
                mime_type=prep_doc.mime_type,
                file_size_bytes=prep_doc.file_size_bytes,
                page_count=prep_doc.page_count,
                docling_result=docling_result,
                ocr_results=ocr_results,
                vlm_corrections=vlm_corrections,
                metrics=metrics,
                s3_bucket=bucket,
                s3_key=s3_key,
                docling_used=bool(docling_result is not None),
                vlm_used=vlm_used,
                vlm_provider=settings.VLM_PROVIDER if vlm_used else None
            )

            clock.lap("serialize")

            # Assign raw OCR text
            parsed_doc.raw_text = parsed_doc.text

            # Step 7: Run OpenRouter LLM Field Extraction on OCR text
            llm_fields: Dict[str, Any] = {}
            try:
                from pipeline.engines.llm_field_extractor import llm_extract_fields
                llm_fields = await asyncio.to_thread(
                    llm_extract_fields,
                    doc_type=doc_type_hint,
                    raw_text=parsed_doc.text,
                    doc_id=document_id,
                )
                clock.lap("llm_field_extraction")
                if llm_fields:
                    import json
                    from idp.services.extraction.field_location_resolver import FieldLocationResolver

                    if not isinstance(parsed_doc.custom_metadata, dict):
                        parsed_doc.custom_metadata = {}
                    parsed_doc.custom_metadata["llm_extracted_fields"] = llm_fields
                    parsed_doc.formatted_text = json.dumps(llm_fields, indent=2)

                    try:
                        resolver = FieldLocationResolver()
                        raw_element_dicts = [elem.model_dump() for elem in parsed_doc.elements]
                        page_dims = [{"width": p.width, "height": p.height} for p in parsed_doc.pages]
                        table_cells_dicts = []
                        for tbl in (parsed_doc.tables or []):
                            for cell in (tbl.cells or []):
                                table_cells_dicts.append({
                                    "id": getattr(cell, "id", None),
                                    "text": cell.text,
                                    "bbox": cell.bbox,
                                    "page_number": tbl.page_number,
                                    "confidence": cell.confidence
                                })

                        field_locs = await asyncio.to_thread(
                            resolver.resolve_field_locations,
                            extracted_fields=llm_fields,
                            ocr_elements=raw_element_dicts,
                            table_cells=table_cells_dicts,
                            page_dimensions=page_dims,
                            debug_mode=True
                        )
                        field_locs_dict = {k: v.model_dump() for k, v in field_locs.items()}
                        ocr_tokens_debug = [t.model_dump() for t in resolver.extract_debug_tokens(raw_element_dicts, page_dims)]
                        parsed_doc.custom_metadata["field_locations"] = field_locs_dict
                        parsed_doc.custom_metadata["ocr_tokens"] = ocr_tokens_debug
                    except Exception as loc_err:
                        logger.warning(format_doc_log(document_id, f"Field location resolution notice: {loc_err}"))
            except Exception as llm_err:
                clock.lap("llm_field_extraction")
                logger.warning(format_doc_log(document_id, f"LLM field extraction notice: {llm_err}"))
            if llm_fields:
                clock.lap("field_locations")
            else:
                clock.skip()

            # Step 8: Upload structured JSON to S3 parsed-documents prefix
            self._stamp_stage_timings(parsed_doc, clock)
            output_location = await self._save_and_upload_output(parsed_doc, document_id, bucket)
            clock.lap("save")

            elapsed = time.time() - start_time
            logger.info(format_doc_log(document_id, f"Node 2 processing completed successfully in {elapsed:.2f}s -> {output_location}"))
            self._log_timing_summary(document_id, clock, elapsed, metrics.docling_model_timings)

            res_dict = {
                "document_id": document_id,
                "status": "completed",
                "output_location": output_location,
                "processing_time_seconds": round(elapsed, 3),
                "raw_text": parsed_doc.text,
                "formatted_text": parsed_doc.formatted_text or "",
                "document_markdown": parsed_doc.document_markdown,
                "extracted_fields": llm_fields,
                "field_locations": parsed_doc.custom_metadata.get("field_locations", {}),
                "ocr_tokens": parsed_doc.custom_metadata.get("ocr_tokens", []),
                "stage_timings": dict(clock.timings),
            }

            if redis_client:
                try:
                    await redis_client.set(
                        f"result:idp:process:{document_id}",
                        json.dumps(res_dict),
                        ex=getattr(settings, "REDIS_LOCK_TIMEOUT_SECONDS", 900)
                    )
                except Exception as r_save_err:
                    logger.debug(format_doc_log(document_id, f"Redis result save note: {r_save_err}"))

            return res_dict

        finally:
            cleanup_temp_dir(temp_dir)


    @staticmethod
    def _stamp_stage_timings(parsed_doc: ParsedDocument, clock: StageClock) -> None:
        """Copy stage timings so far into the ParsedDocument before it is saved (the save itself
        can only appear in the log summary, since it happens after this)."""
        if parsed_doc.processing is not None:
            parsed_doc.processing.metrics.stage_timings = dict(clock.timings)

    @staticmethod
    def _log_timing_summary(
        document_id: str,
        clock: StageClock,
        elapsed: float,
        docling_models: Optional[Dict[str, float]] = None,
    ) -> None:
        """Log where this document's time went, one row per stage."""
        rows = []
        for stage, seconds in clock.timings.items():
            note = ""
            if stage == "docling" and docling_models:
                picked = [k for k in ("layout", "ocr", "table_structure", "page_parse", "reading_order") if k in docling_models]
                note = "Docling models: " + ", ".join(f"{k} {docling_models[k]:.2f}s" for k in picked)
            elif stage in ("lightonocr", "llm_field_extraction"):
                note = "LiteLLM"
            rows.append((stage, seconds, note))
        if rows:
            logger.info(format_doc_log(document_id, format_timing_table("Document timing summary", rows, elapsed)))

    async def _get_page_images(
        self, file_path: str, prep_doc: PreprocessedDocument
    ) -> List[Tuple[bytes, float, float]]:
        """Extract bytes and actual pixel dimensions for each page of PDF or image file.
        
        Returns:
            List of (image_bytes, pixel_width, pixel_height) tuples per page.
        """
        results: List[Tuple[bytes, float, float]] = []
        if prep_doc.file_category == "image":
            with open(file_path, "rb") as f:
                img_bytes = f.read()
            # Detect actual image dimensions
            w, h = self._get_image_dimensions(img_bytes)
            results.append((img_bytes, w, h))
        elif prep_doc.file_category == "pdf":
            try:
                import fitz
                doc = fitz.open(file_path)
                for page in doc:
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    results.append((img_bytes, float(pix.width), float(pix.height)))
                doc.close()
            except Exception:
                # Fallback: direct byte read, dimensions unknown
                with open(file_path, "rb") as f:
                    img_bytes = f.read()
                w, h = self._get_image_dimensions(img_bytes)
                results.append((img_bytes, w, h))
        return results

    @staticmethod
    def _get_image_dimensions(image_bytes: bytes) -> Tuple[float, float]:
        """Get pixel dimensions of an image from its bytes."""
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(image_bytes))
            return float(img.width), float(img.height)
        except Exception:
            return 595.0, 842.0  # Fallback to A4 doc units

    def _recover_comb_grids(
        self,
        docling_result: Optional[DoclingParseResult],
        page_image_data: List[Tuple[bytes, float, float]],
        doc_id: str,
    ) -> int:
        """
        Docling welds a printed comb-box row (label + one hand-written char per
        printed cell) into a SINGLE line element, so the field never gets its
        own bounding box. This reads the printed cell-divider grid straight off
        the page image (CombGridDetector), assigns the recognised value
        characters to their cells, and appends one single-character
        ``LayoutElement`` per cell -- in PDF-point coordinates -- to
        ``docling_result.elements``. The normal serializer + CombBoxDetector
        path then merges them into an accurately-located field token.

        Non-fatal: OpenCV missing, no page image, or no uniform grid found ->
        recovers nothing and leaves the fused row element untouched.

        Returns the number of recovered cell elements.
        """
        if not docling_result or not getattr(docling_result, "elements", None):
            return 0
        if not page_image_data:
            return 0

        from idp.services.extraction.comb_grid_detector import CombGridDetector

        source_elements = [
            e for e in docling_result.elements
            if CombGridDetector.is_fused_comb_row(e.text, e.bbox)
        ]
        if not source_elements:
            return 0

        detector = CombGridDetector()
        dims = docling_result.pages_dimensions or []
        recovered: List[LayoutElement] = []

        for elem in source_elements:
            pno = elem.page_number or 1
            if pno < 1 or pno > len(page_image_data):
                continue
            page_bytes, px_w, px_h = page_image_data[pno - 1]
            if not page_bytes or px_w <= 0 or px_h <= 0:
                continue
            pdf_w = dims[pno - 1].get("width", 595.0) if pno <= len(dims) else 595.0
            pdf_h = dims[pno - 1].get("height", 842.0) if pno <= len(dims) else 842.0

            cells = detector.recover_cell_elements(
                image_bytes=page_bytes,
                elem=elem,
                page_px_w=px_w,
                page_px_h=px_h,
                pdf_w=pdf_w,
                pdf_h=pdf_h,
                doc_id=doc_id,
            )
            recovered.extend(cells)

        if recovered:
            docling_result.elements.extend(recovered)
        return len(recovered)

    async def process_uploaded_file(
        self,
        file_bytes: bytes,
        filename: str,
        document_id: str,
        s3_bucket: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Save direct browser uploaded raw file bytes to S3 raw-documents prefix and process through Node 2.
        """
        bucket = s3_bucket if (isinstance(s3_bucket, str) and s3_bucket.strip()) else settings.S3_BUCKET
        raw_key = f"{current_tenant_id()}/{settings.RAW_DOCUMENT_PREFIX.strip('/')}/{document_id}_{filename}"
        await self.storage.upload(
            key=raw_key,
            content=file_bytes,
            bucket=bucket,
            content_type="application/octet-stream",
            doc_id=document_id
        )

        res = await self.process_document(
            document_id=document_id,
            s3_key=raw_key,
            s3_bucket=bucket
        )

        # Retrieve parsed document model
        parsed = await self.get_parsed_document(document_id, bucket)
        res["result"] = parsed.model_dump() if parsed else None
        res["extracted_fields"] = (parsed.custom_metadata or {}).get("llm_extracted_fields", {}) if (parsed and parsed.custom_metadata) else {}
        return res


    async def get_parsed_document(self, document_id: str, bucket: Optional[str] = None) -> Optional[ParsedDocument]:
        """Retrieve parsed document result model by document_id."""
        target_bucket = bucket if (isinstance(bucket, str) and bucket.strip()) else settings.S3_BUCKET
        if not SAFE_ID_PATTERN.match(document_id or "") or not SAFE_ID_PATTERN.match(target_bucket):
            return None
        out_key = f"{current_tenant_id()}/{settings.PARSED_DOCUMENT_PREFIX.strip('/')}/{document_id}.json"
        
        # Check local mock path first if exists
        local_mock_path = os.path.join(settings.TEMP_DIR, "s3_mock", target_bucket, out_key)
        if os.path.exists(local_mock_path):
            with open(local_mock_path, "r", encoding="utf-8") as f:
                import json
                data = json.load(f)
                return ParsedDocument(**data)
        return None

    async def _save_and_upload_output(self, parsed_doc: ParsedDocument, doc_id: str, bucket: str) -> str:
        output_json = parsed_doc.model_dump_json(indent=2)
        out_key = f"{current_tenant_id()}/{settings.PARSED_DOCUMENT_PREFIX.strip('/')}/{doc_id}.json"
        return await self.storage.upload(
            key=out_key,
            content=output_json,
            bucket=bucket,
            content_type="application/json",
            doc_id=doc_id
        )


# Shared process-wide DocumentProcessor singleton
processor = DocumentProcessor()
