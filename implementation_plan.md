# Plan: Enforce Strict IDP Separation (Port 8000 Never Runs OCR) & Deduplicate Pipeline Scans

## Objective
1. **Zero OCR on Port 8000:** Completely eliminate any fallback that could initialize or execute Docling, RapidOCR, PyTorch, or Hugging Face models inside the FastAPI Port 8000 process.
2. **Deduplicate Celery & `idp_scan`:** Ensure documents processed via background upload tasks write directly to the `s3_extracted` tier so the case verification pipeline (`idp_scan`) instantly reuses the extracted result instead of invoking duplicate OCR runs on Port 8001.
3. **Robust Error Handling on Remote Calls:** Use `resp.raise_for_status()` and handle connection errors cleanly without silent local fallbacks.

---

## User Review Required
> [!IMPORTANT]
> - Port 8000 will **never** attempt to process non-fast-path documents locally if Port 8001 is unreachable or encounters an error. Instead, it will log an explicit failure and mark the document scan as failed in the pipeline state.
> - Fast paths (Aadhaar XML and Loan Agreement pyHanko digital signature check) will remain instantaneous on both Port 8000 and Port 8001 since they require zero OCR or heavy deep learning models.

---

## Proposed Changes

### Component 1: Pipeline IDP Scan Node ([`idp_scan.py`](file:///d:/Projects/Automated%20Disbursment%20Scorecard/pipeline/nodes/idp_scan.py))
- **Remove Local Docling Fallback:** Remove `get_processor().process_document(...)` execution inside `_process_single_document`.
- **Enforce HTTP Delegation:**
  - Fast-paths (XML, Loan Agreement pyHanko) run instantly via `DocumentSerializer`.
  - All other documents (KYC, Statements, KFS, etc.) make an HTTP POST to `http://127.0.0.1:8001/api/v1/documents/process`.
  - Validate responses with `resp.raise_for_status()`.
  - If Port 8001 is unavailable or times out, log a structured warning and return `None` (or error dict) without touching local models.

### Component 2: Celery Background Ingestion ([`celery_app.py`](file:///d:/Projects/Automated%20Disbursment%20Scorecard/pipeline/celery_app.py))
- **Sync Celery Output to S3 Extracted Tier:**
  - When `process_document_task(doc_id, file_path, case_id)` finishes, write the extracted result to `s3_extracted/{case_id}/{doc_key}.json` as well as updating `document_registry`.
  - When a user uploads documents in the UI and then clicks "Start Verification", `idp_scan` will immediately hit the S3 cache and finish in < 2 seconds without triggering redundant OCR runs on Port 8001.

### Component 3: Test Suite Verification
- Update unit tests in [`tests/`](file:///d:/Projects/Automated%20Disbursment%20Scorecard/tests/) to verify:
  1. `_process_single_document` does not invoke local Docling on remote failure.
  2. `ProcessingMetadata` in tests includes required fields (e.g. `page_count=1`).
  3. Fast paths and Celery S3 extraction sync behave deterministically.

---

## Verification Plan

### Automated Tests
- Run full unit tests:
  ```powershell
  pytest tests/test_loan_agreement_pyhanko.py tests/test_aadhaar_xml_comparison.py tests/idp/unit/test_serializer.py -v
  ```

### End-to-End Verification
- Check Port 8000 logs during case execution to confirm zero `[DoclingCache]`, `[RapidOCR]`, or HuggingFace initialization messages.
- Verify Port 8001 handles OCR without memory leaks or CPU thread contention.
