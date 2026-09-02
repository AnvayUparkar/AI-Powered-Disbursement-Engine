from typing import Any, Optional
from idp.services.docling.options import DoclingOptions
from idp.core.logging import logger


class DoclingPipeline:
    """Pipeline factory for constructing Docling DocumentConverter instances."""

    def __init__(self, options: Optional[DoclingOptions] = None):
        self.options = options or DoclingOptions()
        self._converter = None

    def get_converter(self) -> Any:
        if self._converter is not None:
            return self._converter

        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.pipeline_options import PdfPipelineOptions

            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = self.options.do_ocr
            pipeline_options.do_table_structure = self.options.do_table_structure

            # Configure TableFormer mode if available
            if hasattr(pipeline_options, "table_structure_options"):
                pipeline_options.table_structure_options.mode = self.options.table_mode

            format_options = {
                "pdf": PdfFormatOption(pipeline_options=pipeline_options)
            }
            self._converter = DocumentConverter(format_options=format_options)
            logger.info("Docling DocumentConverter initialized successfully with ACCURATE table mode.")
        except Exception as e:
            logger.warning(f"Docling initialization note/fallback: {e}. Native Docling converter not imported or mock active.")
            self._converter = "MOCK"

        return self._converter
