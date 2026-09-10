import os
import time
import asyncio
from typing import Optional, Dict, List, Tuple, Any
from idp.services.storage.s3 import S3Storage
from idp.services.document_preprocessor import DocumentPreprocessor, PreprocessedDocument
from idp.services.docling.parser import DoclingParser, DoclingParseResult
from idp.services.docling.options import DoclingOptions
from config.doc_types import get_canonical_doc_type
from config.docling_profiles import get_profile_for_document_type
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
        # One DoclingParser per document-type profile (config/docling_profiles.py),
        # built lazily and cached so each profile's DocumentConverter is warmed up
        # exactly once (see DoclingPipeline -> get_cached_converter, which itself
        # caches by options fingerprint) rather than rebuilt every time the
        # document-type mix changes mid-batch.
        self._docling_parsers: Dict[str, DoclingParser] = {}
        # Standalone OCR engine bypassed — Docling is primary engine
        self.ocr_engine = None
        self.ocr_router = None
        self.router = ConfidenceRouter()
        self.vlm_client = VLMClient()
        self.serializer = DocumentSerializer()

    def _get_docling_parser(self, doc_type: str) -> DoclingParser:
        """Return the cached DoclingParser tuned for this canonical document type."""
        parser = self._docling_parsers.get(doc_type)
        if parser is None:
            profile: DoclingOptions = get_profile_for_document_type(doc_type)
            parser = DoclingParser(profile)
            self._docling_parsers[doc_type] = parser
        return parser

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
            doc_type_hint = get_canonical_doc_type(filename)
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

            # Step 3: Docling layout + integrated OCR parsing (runs on all document types),
            # using the DoclingOptions profile tuned for this document's canonical type
            # (character-box KYC forms, scanned bank statements, digital PDFs, etc.)
            docling_start = time.time()
            docling_result: Optional[DoclingParseResult] = None
            try:
                docling_parser = self._get_docling_parser(doc_type_hint)
                docling_result = docling_parser.parse(local_file_path, doc_id=document_id)
            except Exception as e:
                logger.warning(format_doc_log(document_id, f"Docling parsing warning: {e}. Proceeding with fallback parsing."))
            metrics.docling_processing_time = round(time.time() - docling_start, 3)

            # Step 4: Capture page images for VLM region cropping (no standalone OCR)
            page_image_data = await self._get_page_images(local_file_path, prep_doc)
            page_images: List[bytes] = [item[0] for item in page_image_data]
            ocr_results: List[OCRResult] = []

            # Step 5: Selective VLM Fallback Routing on Docling layout/OCR elements
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

            # Assign raw OCR text
            parsed_doc.raw_text = parsed_doc.text

            # Step 7: Run OpenRouter LLM Field Extraction on OCR text
            llm_fields: Dict[str, Any] = {}
            try:
                from pipeline.engines.llm_field_extractor import llm_extract_fields
                llm_fields = llm_extract_fields(
                    doc_type=doc_type_hint,
                    raw_text=parsed_doc.text,
                    doc_id=document_id,
                )
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

                        field_locs = resolver.resolve_field_locations(
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
                logger.warning(format_doc_log(document_id, f"LLM field extraction notice: {llm_err}"))

            # Step 8: Upload structured JSON to S3 parsed-documents prefix
            output_location = await self._save_and_upload_output(parsed_doc, document_id, bucket)

            elapsed = time.time() - start_time
            logger.info(format_doc_log(document_id, f"Node 2 processing completed successfully in {elapsed:.2f}s -> {output_location}"))

            return {
                "document_id": document_id,
                "status": "completed",
                "output_location": output_location,
                "processing_time_seconds": round(elapsed, 3),
                "raw_text": parsed_doc.text,
                "formatted_text": parsed_doc.formatted_text or "",
                "extracted_fields": llm_fields,
                "field_locations": parsed_doc.custom_metadata.get("field_locations", {}),
                "ocr_tokens": parsed_doc.custom_metadata.get("ocr_tokens", []),
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
        res["extracted_fields"] = (parsed.custom_metadata or {}).get("llm_extracted_fields", {}) if (parsed and parsed.custom_metadata) else {}
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


# Shared process-wide DocumentProcessor singleton
processor = DocumentProcessor()
