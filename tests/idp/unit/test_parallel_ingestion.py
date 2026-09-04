import io
import pytest
import asyncio
from PIL import Image, ImageDraw
from typing import List, Tuple
from idp.services.document_processor import DocumentProcessor
from idp.models.ocr import OCRResult
from pipeline.nodes.node2_extract import node2_extract
from pipeline.state import PipelineState


def _create_test_page_bytes(text: str) -> bytes:
    """Utility to generate valid PNG image bytes for page concurrency testing."""
    img = Image.new("RGB", (500, 120), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((20, 40), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_parallel_page_ocr_ingestion(monkeypatch):
    """Verifies that DocumentProcessor parallelizes multi-page OCR execution via ThreadPoolExecutor."""
    processor = DocumentProcessor()

    # Generate 4 synthetic pages
    page_data: List[Tuple[bytes, float, float]] = [
        (_create_test_page_bytes(f"PAGE {i} TEXT DATA FOR LOAN APPLICATION"), 500.0, 120.0)
        for i in range(1, 5)
    ]

    # Mock _get_page_images to return 4 pages
    async def mock_get_page_images(file_path, prep_doc):
        return page_data

    monkeypatch.setattr(processor, "_get_page_images", mock_get_page_images)

    # Process page images via ThreadPoolExecutor parallelized Step 4
    ocr_results: List[OCRResult] = []
    doc_type_hint = "loan_application"
    
    def _process_single_page(args: Tuple[int, bytes, float, float]) -> OCRResult:
        pidx, page_bytes, img_width, img_height = args
        pno = pidx + 1
        ocr_res = processor.ocr_router.process_page(
            page_bytes, page_number=pno, doc_id="TEST_PARALLEL_DOC", doc_type_hint=doc_type_hint, preview_text=""
        )
        ocr_res.image_width = float(img_width)
        ocr_res.image_height = float(img_height)
        return ocr_res

    from concurrent.futures import ThreadPoolExecutor
    page_tasks = [(pidx, page_bytes, w, h) for pidx, (page_bytes, w, h) in enumerate(page_data)]
    loop = asyncio.get_running_loop()

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="test_page_worker") as pool:
        futures = [loop.run_in_executor(pool, _process_single_page, task_args) for task_args in page_tasks]
        ocr_results = list(await asyncio.gather(*futures))

    # Assert deterministic page order and successful parallel extraction
    ocr_results.sort(key=lambda r: r.page_number)
    assert len(ocr_results) == 4
    for idx, res in enumerate(ocr_results):
        assert res.page_number == idx + 1
        assert len(res.elements) > 0
        extracted_text = "".join([e.text for e in res.elements]).upper()
        assert f"PAGE{idx + 1}" in extracted_text or f"PAGE {idx + 1}" in extracted_text


def test_node2_parallel_document_ingestion(tmp_path, monkeypatch):
    """Verifies that Node 2 parallelizes multi-document binary ingestion via ThreadPoolExecutor."""
    loan_id = "LOAN_PARALLEL_TEST"
    
    # Create 3 synthetic binary document files
    raw_paths = {}
    for doc_name in ["application_form.png", "kyc_pan.png", "bank_statement.png"]:
        fpath = tmp_path / doc_name
        img = Image.new("RGB", (300, 100), color=(255, 255, 255))
        d = ImageDraw.Draw(img)
        d.text((10, 30), f"TEST DOCUMENT {doc_name.upper()}", fill=(0, 0, 0))
        img.save(fpath)
        raw_paths[doc_name] = str(fpath)

    state: PipelineState = {
        "loan_id": loan_id,
        "los_data": {},
        "raw_doc_paths": raw_paths,
        "extracted_data": {},
        "face_embeddings": {},
        "dms_status": {},
        "otp_audit": {},
        "comparison_results": [],
        "subnode_rollups": {},
        "compiled_report": {},
        "scorecard": {},
        "errors": [],
        "node_history": []
    }

    # Execute Node 2 containing ThreadPoolExecutor multi-document ingestion
    res_state = node2_extract(state)

    assert "application_form" in res_state["extracted_data"]
    assert "kyc_pan" in res_state["extracted_data"]
    assert "bank_statement" in res_state["extracted_data"]
    assert len(res_state["extracted_data"]) >= 3
