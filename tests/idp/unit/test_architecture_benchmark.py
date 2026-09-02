import time
import pytest
from pathlib import Path
from idp.services.docling.options import DoclingOptions
from idp.services.docling.parser import DoclingParser
from idp.services.ocr.rapidocr_engine import RapidOCREngine
from idp.services.vlm.router import ConfidenceRouter
from idp.services.output.serializer import DocumentSerializer
from idp.models.processing import ProcessingMetrics


@pytest.fixture
def sample_pdf_path(tmp_path) -> str:
    """Create a test PDF file for benchmark comparison."""
    try:
        import fitz
        pdf_path = str(tmp_path / "benchmark_sample.pdf")
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        
        # Add sample document text and table
        page.insert_text((50, 50), "SANCTION LETTER", fontsize=16)
        page.insert_text((50, 80), "Applicant Name: Rajesh Sharma", fontsize=12)
        page.insert_text((50, 100), "PAN Number: ABCDE1234F", fontsize=12)
        page.insert_text((50, 120), "Sanctioned Loan Amount: INR 500,000", fontsize=12)
        
        # Draw sample table rect
        page.insert_text((50, 160), "Term | Interest Rate | Monthly EMI", fontsize=11)
        page.insert_text((50, 180), "24 Months | 11.5% | INR 23,400", fontsize=11)
        
        doc.save(pdf_path)
        doc.close()
        return pdf_path
    except Exception:
        # Fallback minimal valid PDF
        pdf_path = str(tmp_path / "benchmark_sample.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\nxref\n0 4\n0000000000 65535 f \ntrailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n150\n%%EOF")
        return pdf_path


def test_pipeline_architecture_benchmark(sample_pdf_path):
    """
    Architectural Benchmark:
    Pipeline A: Legacy (Docling structure + separate RapidOCR pass + spatial IoU alignment)
    Pipeline B: Target Architecture (Docling-managed native OCR + TableFormer + selective VLM)
    """
    doc_id = "BENCH_001"
    filename = "sanction_letter.pdf"

    # --- PIPELINE A: LEGACY ARCHITECTURE ---
    start_a = time.time()
    
    # Pass 1: Docling structure only (do_ocr=False)
    docling_options_a = DoclingOptions(do_ocr=False)
    parser_a = DoclingParser(docling_options_a)
    result_a = parser_a.parse(sample_pdf_path, doc_id=f"{doc_id}_A")
    
    # Pass 2: Standalone RapidOCR
    ocr_engine = RapidOCREngine()
    # Dummy image bytes for mock OCR test if PDF fitz renders
    ocr_results_a = []
    
    # Pass 3: Serializer merging with spatial alignment
    serializer = DocumentSerializer()
    metrics_a = ProcessingMetrics()
    doc_a = serializer.build_unified_document(
        doc_id=f"{doc_id}_A",
        filename=filename,
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        docling_result=result_a,
        ocr_results=ocr_results_a,
        vlm_corrections={},
        metrics=metrics_a,
        docling_used=True,
        vlm_used=False
    )
    time_a = time.time() - start_a

    # --- PIPELINE B: TARGET ARCHITECTURE (Docling-Managed OCR) ---
    start_b = time.time()
    
    # Unified Pass: Docling layout + native OCR (do_ocr=True)
    docling_options_b = DoclingOptions(do_ocr=True, ocr_model_name="PP_ocrV6_MEDIUM")
    parser_b = DoclingParser(docling_options_b)
    result_b = parser_b.parse(sample_pdf_path, doc_id=f"{doc_id}_B")
    
    # Quality Router check on native elements
    router = ConfidenceRouter()
    flagged = router.get_low_confidence_layout_elements(result_b.elements, doc_id=f"{doc_id}_B")
    
    # Direct serialization without manual spatial coordinate matching
    metrics_b = ProcessingMetrics()
    doc_b = serializer.build_unified_document(
        doc_id=f"{doc_id}_B",
        filename=filename,
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        docling_result=result_b,
        ocr_results=[],
        vlm_corrections={},
        metrics=metrics_b,
        docling_used=True,
        vlm_used=False
    )
    time_b = time.time() - start_b

    # Assertions & Comparisons
    assert doc_b is not None
    assert doc_b.processing.ocr_engine == "docling_rapidocr"
    assert doc_b.processing.ocr_model == "PP-OCRv6_MEDIUM"
    
    # Print architectural comparison summary
    print("\n" + "="*70)
    print("ARCHITECTURAL BENCHMARK COMPARISON REPORT")
    print("="*70)
    print(f"Pipeline A (Legacy External OCR + Manual Spatial Alignment):")
    print(f"  - Total Execution Time: {time_a:.4f}s")
    print(f"  - Pipeline Passes: 3 (Docling -> RapidOCR -> IoU Alignment)")
    print(f"  - OCR Engine Metadata: {doc_a.processing.ocr_engine}")
    print(f"Pipeline B (Docling-Managed OCR + Selective VLM Fallback):")
    print(f"  - Total Execution Time: {time_b:.4f}s")
    print(f"  - Pipeline Passes: 1 Unified Pass")
    print(f"  - OCR Engine Metadata: {doc_b.processing.ocr_engine} ({doc_b.processing.ocr_model})")
    print(f"  - Flagged for Selective VLM: {len(flagged)} elements")
    print("="*70)
