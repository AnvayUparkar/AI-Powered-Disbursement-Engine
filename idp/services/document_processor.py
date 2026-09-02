import os
import time
import asyncio
from typing import Optional, Dict, List, Tuple, Any
from idp.services.storage.s3 import S3Storage
from idp.services.document_preprocessor import DocumentPreprocessor, PreprocessedDocument
from idp.services.docling.parser import DoclingParser, DoclingParseResult
from idp.services.ocr.rapidocr_engine import RapidOCREngine, OCRResult
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
        bucket = s3_bucket or settings.S3_BUCKET
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

            # Step 3: Docling layout + integrated OCR parsing
            docling_start = time.time()
            docling_result: Optional[DoclingParseResult] = None
            try:
                docling_result = self.docling_parser.parse(local_file_path, doc_id=document_id)
            except Exception as e:
                logger.warning(format_doc_log(document_id, f"Docling parsing warning: {e}. Proceeding with fallback parsing."))
            metrics.docling_processing_time = round(time.time() - docling_start, 3)

            page_image_data = await self._get_page_images(local_file_path, prep_doc)
            page_images: List[bytes] = [item[0] for item in page_image_data]

            # Step 4: Selective VLM Fallback Routing on Docling layout/OCR elements
            vlm_start = time.time()
            vlm_corrections: Dict[str, VLMResult] = {}
            vlm_used = False

            if docling_result and docling_result.elements:
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
                        bbox=elem.bbox,
                        page_width=img_w,
                        page_height=img_h
                    )

                    vlm_res = await self.vlm_client.analyze_region(
                        image_bytes=cropped_bytes or page_bytes,
                        ocr_element=elem,
                        context_hint=f"Page {pno} element {elem.id}",
                        doc_id=document_id
                    )

                    if vlm_res and elem.id:
                        vlm_corrections[elem.id] = vlm_res
                        metrics.vlm_fallback_count += 1
                        vlm_used = True

            metrics.vlm_processing_time = round(time.time() - vlm_start, 3)
            metrics.total_processing_time = round(time.time() - start_time, 3)

            # Step 5: Serialize into Canonical Unified Document Representation
            parsed_doc = self.serializer.build_unified_document(
                doc_id=document_id,
                filename=filename,
                mime_type=prep_doc.mime_type,
                file_size_bytes=prep_doc.file_size_bytes,
                page_count=prep_doc.page_count,
                docling_result=docling_result,
                ocr_results=[],
                vlm_corrections=vlm_corrections,
                metrics=metrics,
                s3_bucket=bucket,
                s3_key=s3_key,
                docling_used=True,
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
        bucket = s3_bucket or settings.S3_BUCKET
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
        target_bucket = bucket or settings.S3_BUCKET
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
