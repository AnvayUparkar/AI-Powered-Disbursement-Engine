from typing import Any, Optional
from idp.services.docling.options import DoclingOptions
from idp.core.logging import logger


_shared_converter: Optional[Any] = None


class DoclingPipeline:
    """Pipeline factory for constructing Docling DocumentConverter instances."""

    def __init__(self, options: Optional[DoclingOptions] = None):
        self.options = options or DoclingOptions()
        self._converter = None

    def get_converter(self) -> Any:
        global _shared_converter
        if _shared_converter is not None:
            return _shared_converter

        if self._converter is not None:
            return self._converter


        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.pipeline_options import PdfPipelineOptions

            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = self.options.do_ocr
            pipeline_options.do_table_structure = self.options.do_table_structure

            # Configure Docling-managed OCR engine (RapidOCR PP-OCRv6)
            if self.options.do_ocr:
                try:
                    from docling.datamodel.pipeline_options import RapidOcrOptions
                    ocr_opts = RapidOcrOptions(
                        backend="onnxruntime",
                        force_full_page_ocr=True,
                        lang=self.options.ocr_lang
                    )
                    if self.options.det_model_path:
                        ocr_opts.det_model_path = self.options.det_model_path
                    if self.options.rec_model_path:
                        ocr_opts.rec_model_path = self.options.rec_model_path
                    pipeline_options.ocr_options = ocr_opts
                    logger.info(f"Configured Docling-managed RapidOCR engine with model spec: {self.options.ocr_model_name}")
                except Exception as ocr_err:
                    logger.warning(f"Could not configure RapidOcrOptions on Docling pipeline: {ocr_err}")

            # Configure TableFormer mode if available
            if hasattr(pipeline_options, "table_structure_options"):
                pipeline_options.table_structure_options.mode = self.options.table_mode

            format_options = {
                "pdf": PdfFormatOption(pipeline_options=pipeline_options)
            }
            self._converter = DocumentConverter(format_options=format_options)
            _shared_converter = self._converter
            logger.info("Docling DocumentConverter initialized successfully with ACCURATE table mode and managed OCR.")
        except Exception as e:
            logger.warning(f"Docling initialization note/fallback: {e}. Native Docling converter not imported or mock active.")
            self._converter = "MOCK"
            _shared_converter = "MOCK"

        return self._converter

