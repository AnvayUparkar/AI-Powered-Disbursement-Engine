import os
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, List, Tuple, Any
from idp.services.storage.s3 import S3Storage
from idp.services.document_preprocessor import DocumentPreprocessor, PreprocessedDocument
from idp.services.docling.parser import DoclingParser, DoclingParseResult
from idp.services.ocr.rapidocr_engine import RapidOCREngine, OCRResult
from idp.services.ocr.ocr_model_router import OCRModelRouter
from idp.services.vlm.router import ConfidenceRouter
from idp.services.vlm.client import VLMClient, VLMResult
from idp.services.output.serializer import DocumentSerializer
from idp.models.document import ParsedDocument
from idp.models.processing import ProcessingMetrics
from idp.utils.file_utils import create_temp_dir, cleanup_temp_dir
from idp.utils.image_utils import crop_image_region
from idp.core.config import settings
from idp.core.logging import logger, format_doc_log


class DocumentProcessor:
    """Core Node 2 Document Processing Pipeline Orchestrator."""

    def __init__(self):
        self.storage = S3Storage()
        self.preprocessor = DocumentPreprocessor()
        self.docling_parser = DoclingParser()
        self.ocr_engine = RapidOCREngine()
        self.ocr_router = OCRModelRouter(default_engine=self.ocr_engine)
        self.router = ConfidenceRouter()
        self.vlm_client = VLMClient()
        self.serializer = DocumentSerializer()

    async def process_document(
        self,
        document_id: str,
        s3_key: str,
        s3_bucket: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Executes end-to-end processing lifecycle for a single document.

        Returns:
            Dict containing document_id, status, output_location, and processing_time.
        """
        start_time = time.time()
        bucket = s3_bucket if (isinstance(s3_bucket, str) and s3_bucket.strip()) else settings.S3_BUCKET
        temp_dir = create_temp_dir(prefix=f"node2_{document_id}_")

        logger.info(format_doc_log(document_id, f"Beginning Node 2 processing for s3://{bucket}/{s3_key}"))

        try:
            # Step 1: Download raw document from S3
            filename = os.path.basename(s3_key)
            local_file_path = os.path.join(temp_dir, filename)
            await self.storage.download(key=s3_key, dest_path=local_file_path, bucket=bucket, doc_id=document_id)

            # Step 2: Preprocess and validate document
            prep_doc: PreprocessedDocument = self.preprocessor.preprocess(local_file_path, doc_id=document_id)

            metrics = ProcessingMetrics()

            # Fast path for XML documents
            if prep_doc.file_category == "xml":
                parsed_doc = self.serializer.parse_xml_fast_path(
                    file_path=local_file_path,
                    doc_id=document_id,
                    s3_bucket=bucket,
                    s3_key=s3_key
                )
                output_location = await self._save_and_upload_output(parsed_doc, document_id, bucket)
                elapsed = time.time() - start_time
                return {
                    "document_id": document_id,
                    "status": "completed",
                    "output_location": output_location,
                    "processing_time_seconds": round(elapsed, 3)
                }

            # Step 3: Docling layout parsing (bypassed for KYC and non-tabular image documents)
            docling_start = time.time()
            docling_result: Optional[DoclingParseResult] = None
            is_kyc_or_image = (
                prep_doc.file_category == "image"
                or any(k in document_id.lower() or k in local_file_path.lower() for k in ["pan", "aadhaar", "kyc", "photo", "sign"])
            )
            if not is_kyc_or_image:
                try:
                    docling_result = self.docling_parser.parse(local_file_path, doc_id=document_id)
                except Exception as e:
                    logger.warning(format_doc_log(document_id, f"Docling parsing warning: {e}. Proceeding with OCR."))
            else:
                logger.info(format_doc_log(document_id, f"Skipping Docling table analysis for KYC / image document: {document_id}"))
            metrics.docling_processing_time = round(time.time() - docling_start, 3)

            # Step 4: RapidOCR + Multilingual Router execution (Parallelized via ThreadPoolExecutor)
            ocr_start = time.time()
            ocr_results: List[OCRResult] = []
            
            # Convert PDF to page images, capturing actual rendered image dimensions
            page_image_data = await self._get_page_images(local_file_path, prep_doc)
            page_images: List[bytes] = [item[0] for item in page_image_data]
            
            doc_type_hint = os.path.splitext(os.path.basename(local_file_path))[0]

            def _process_single_page(args: Tuple[int, bytes, float, float]) -> OCRResult:
                pidx, page_bytes, img_width, img_height = args
                pno = pidx + 1
                preview_text = ""
                if docling_result and docling_result.elements:
                    p_elems = [e for e in docling_result.elements if e.page_number == pno and e.text]
                    preview_text = " ".join([e.text for e in p_elems[:10]])

                ocr_res: OCRResult = self.ocr_router.process_page(
                    page_bytes, page_number=pno, doc_id=document_id, doc_type_hint=doc_type_hint, preview_text=preview_text
                )
                ocr_res.image_width = float(img_width)
                ocr_res.image_height = float(img_height)
                return ocr_res

            max_page_workers = getattr(settings, "MAX_PAGE_WORKERS", 4)
            page_tasks = [
                (pidx, page_bytes, img_w, img_h)
                for pidx, (page_bytes, img_w, img_h) in enumerate(page_image_data)
            ]

            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=min(len(page_tasks) or 1, max_page_workers), thread_name_prefix="idp_page_worker") as pool:
                futures = [loop.run_in_executor(pool, _process_single_page, task_args) for task_args in page_tasks]
                if futures:
                    ocr_results = list(await asyncio.gather(*futures))

            # Ensure page results remain strictly ordered by page_number
            ocr_results.sort(key=lambda r: r.page_number)



            metrics.ocr_processing_time = round(time.time() - ocr_start, 3)

            # Step 5: Evaluate OCR quality and VLM fallback routing
            vlm_start = time.time()
            vlm_corrections: Dict[str, VLMResult] = {}
            vlm_used = False

            for ocr_res in ocr_results:
                if self.router.should_use_vlm(ocr_res, doc_id=document_id):
                    low_conf_elements = self.router.get_low_confidence_elements(ocr_res)
                    metrics.ocr_low_confidence_count += len(low_conf_elements)

                    pno = ocr_res.page_number
                    page_bytes = page_images[pno - 1] if pno <= len(page_images) else b""
                    # Use actual rendered image dims for VLM crop, not Docling doc-unit dims
                    img_w = ocr_res.image_width if ocr_res.image_width > 0 else 595.0
                    img_h = ocr_res.image_height if ocr_res.image_height > 0 else 842.0

                    for elem in low_conf_elements:
                        cropped_bytes = crop_image_region(
                            image_bytes=page_bytes,
                            bbox=elem.bbox,
                            page_width=img_w,
                            page_height=img_h
                        )

                        vlm_res = await self.vlm_client.analyze_region(
                            image_bytes=cropped_bytes or page_bytes,
                            ocr_element=elem,
                            context_hint=f"Page {pno} line {elem.line_number}",
                            doc_id=document_id
                        )

                        if vlm_res and elem.id:
                            vlm_corrections[elem.id] = vlm_res
                            # Update element in-place to ensure downstream serializers and alignment directly use the corrected text
                            elem.ocr_original = elem.text
                            elem.text = vlm_res.text
                            elem.confidence = max(elem.confidence, vlm_res.confidence)
                            elem.source = "vlm_corrected"
                            elem.needs_vlm = False
                            metrics.vlm_fallback_count += 1
                            vlm_used = True

                            # Gentle pacing between VLM fallback calls to respect API quotas
                            await asyncio.sleep(0.25)


            metrics.vlm_processing_time = round(time.time() - vlm_start, 3)
            metrics.total_processing_time = round(time.time() - start_time, 3)

            # Step 6: Serialize into Canonical Unified Document Representation
            parsed_doc = self.serializer.build_unified_document(
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

            # Step 7: Upload structured JSON to S3 parsed-documents prefix
            output_location = await self._save_and_upload_output(parsed_doc, document_id, bucket)

            elapsed = time.time() - start_time
            logger.info(format_doc_log(document_id, f"Node 2 processing completed successfully in {elapsed:.2f}s -> {output_location}"))

            return {
                "document_id": document_id,
                "status": "completed",
                "output_location": output_location,
                "processing_time_seconds": round(elapsed, 3)
            }

        finally:
            cleanup_temp_dir(temp_dir)

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
        raw_key = f"{settings.RAW_DOCUMENT_PREFIX.strip('/')}/{document_id}_{filename}"
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
        return res

    async def get_parsed_document(self, document_id: str, bucket: Optional[str] = None) -> Optional[ParsedDocument]:
        """Retrieve parsed document result model by document_id."""
        target_bucket = bucket if (isinstance(bucket, str) and bucket.strip()) else settings.S3_BUCKET
        out_key = f"{settings.PARSED_DOCUMENT_PREFIX.strip('/')}/{document_id}.json"
        
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
        out_key = f"{settings.PARSED_DOCUMENT_PREFIX.strip('/')}/{doc_id}.json"
        return await self.storage.upload(
            key=out_key,
            content=output_json,
            bucket=bucket,
            content_type="application/json",
            doc_id=doc_id
        )
