import type { DocumentRecord } from '@/types';
import { field, step } from './helpers';

const base = (
  id: string,
  name: string,
  type: DocumentRecord['type'],
  pages: number,
  confidence: number,
  vlmUsed: boolean,
  caseId: string,
  uploadedAt: string,
  extractedFields: DocumentRecord['extractedFields'],
  processingSteps: DocumentRecord['processingSteps'],
  sizeKb = 240,
  rawText?: string
): DocumentRecord => ({
  id,
  name,
  type,
  pages,
  ocrStatus: 'COMPLETED',
  extractionStatus: 'COMPLETED',
  confidence,
  vlmUsed,
  uploadedAt,
  caseId,
  sizeKb,
  extractedFields,
  processingSteps,
  rawText: rawText || extractedFields.map((f) => `${f.name}: ${f.value}`).join('\n'),
});


export const documents: DocumentRecord[] = [
  // User Verified Aadhaar Document
  base('DOC-313347', 'DOC-313347_Jainam_Aadhar .pdf', 'Aadhaar', 1, 98.8, true, 'HDB-2026-001245', '12:54:34', [
    field('Country', 'भारत सरकार / Government of India', 98.8, 'DOC-313347', 1),
    field('Authority', 'भारतीय विशिष्ट ओळख प्राधिकरण / Unique Identification Authority of India', 97.5, 'DOC-313347', 1),
    field('Enrolment No', '0000/00367/28154', 96.0, 'DOC-313347', 1),
    field('Name (Devanagari)', 'जैनम संपत परमार', 98.0, 'DOC-313347', 1),
    field('Name (English)', 'Jainam Sampat Parmar', 99.2, 'DOC-313347', 1),
    field('Father Name', 'S/O Sampat Parmar', 95.0, 'DOC-313347', 1),
    field('Aadhaar Number', '7241 5860 0518', 99.5, 'DOC-313347', 1),
    field('DOB', '08/03/2005', 97.0, 'DOC-313347', 1),
    field('Gender', 'पुरुष/ MALE', 95.0, 'DOC-313347', 1),
    field('Address', '१४,अनुसया निवास, बोरबा देवी मंदिर जवळ, बोरला गोवंडी, मुंबई, महाराष्ट्र - 400088', 96.5, 'DOC-313347', 1),
    field('Mobile', '9769384850', 99.0, 'DOC-313347', 1),
    field('Tagline', 'माझे आधार, माझी ओळख', 98.2, 'DOC-313347', 1),
  ], [
    step('Docling', 'COMPLETED', 'Docling parsed layout structure (0.15s)', '12:54:34', '12:54:36', 99.0),
    step('PaddleOCR', 'COMPLETED', 'RapidOCR Devanagari PP-OCRv5 extracted text (0.65s)', '12:54:36', '12:54:40', 98.5),
    step('VLM Fallback', 'COMPLETED', 'VLM direct visual pixel verification applied', '12:54:40', '12:54:42', 99.0),
  ]),
  // Case 1

  base('doc-001245-appform', 'Application_Form.pdf', 'Application Form', 4, 97.8, false, 'HDB-2026-001245', '10:42:01', [
    field('Applicant Name', 'Rahul Sharma', 98.1, 'doc-001245-appform', 1),
    field('Loan Amount', 90000, 98.4, 'doc-001245-appform', 2),
    field('Login Date', '2026-08-12', 99.0, 'doc-001245-appform', 1),
    field('Signature Present', 'Yes', 97.2, 'doc-001245-appform', 4),
  ], [step('Docling', 'COMPLETED', 'Document parsed', '10:42:04', '10:42:07', 99.1), step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:42:07', '10:42:14', 97.8), step('Field Extraction', 'COMPLETED', 'Completed', '10:42:14', '10:42:27', 97.2)]),
  base('doc-001245-pan', 'PAN.pdf', 'PAN', 1, 98.2, false, 'HDB-2026-001245', '10:42:01', [
    field('PAN Number', 'XXXXX1234X', 98.2, 'doc-001245-pan', 1),
    field('Name', 'Rahul Sharma', 98.0, 'doc-001245-pan', 1),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:42:04', '10:42:06', 99.3), step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:42:06', '10:42:09', 98.2), step('Field Extraction', 'COMPLETED', 'Completed', '10:42:09', '10:42:12', 98.2)]),
  base('doc-001245-aadhaar', 'Aadhaar.pdf', 'Aadhaar', 2, 96.4, false, 'HDB-2026-001245', '10:42:01', [
    field('Aadhaar Number', 'XXXX XXXX 1234', 96.4, 'doc-001245-aadhaar', 1),
    field('Name', 'Rahul Sharma', 96.2, 'doc-001245-aadhaar', 1),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:42:04', '10:42:06', 98.7), step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:42:06', '10:42:10', 96.4), step('Field Extraction', 'COMPLETED', 'Completed', '10:42:10', '10:42:13', 96.4)]),
  base('doc-001245-kyc', 'KYC_Bundle.pdf', 'KYC', 6, 96.0, false, 'HDB-2026-001245', '10:42:01', [
    field('Name Match', 'Rahul Sharma', 96.2, 'doc-001245-kyc', 1),
    field('Address', 'Verified', 95.8, 'doc-001245-kyc', 3),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:42:04', '10:42:07', 98.5), step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:42:07', '10:42:12', 96.0), step('Field Extraction', 'COMPLETED', 'Completed', '10:42:12', '10:42:16', 96.0)]),
  base('doc-001245-selfie', 'Selfie.jpg', 'Miscellaneous', 1, 98.9, false, 'HDB-2026-001245', '10:42:01', [
    field('Live Photo', 'Detected', 98.9, 'doc-001245-selfie', 1),
  ], [step('VLM Fallback', 'COMPLETED', 'Live photo verified', '10:42:08', '10:42:11', 98.9), step('Field Extraction', 'COMPLETED', 'Completed', '10:42:11', '10:42:13', 98.9)]),
  base('doc-001245-agreement', 'Loan_Agreement.pdf', 'Loan Agreement', 8, 97.2, false, 'HDB-2026-001245', '10:42:01', [
    field('Signed', 'Yes', 97.5, 'doc-001245-agreement', 3),
    field('Agreement Date', '2026-08-14', 96.9, 'doc-001245-agreement', 1),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:42:04', '10:42:08', 99.0), step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:42:08', '10:42:14', 97.2), step('Field Extraction', 'COMPLETED', 'Completed', '10:42:14', '10:42:19', 97.2)]),
  base('doc-001245-kfs', 'KFS.pdf', 'KFS', 3, 95.7, false, 'HDB-2026-001245', '10:42:01', [
    field('Interest Rate', '14.5%', 96.1, 'doc-001245-kfs', 1),
    field('Processing Fee', '₹1,000', 95.3, 'doc-001245-kfs', 2),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:42:04', '10:42:06', 98.6), step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:42:06', '10:42:11', 95.7), step('Field Extraction', 'COMPLETED', 'Completed', '10:42:11', '10:42:15', 95.7)]),
  base('doc-001245-sanction', 'Sanction_Letter.pdf', 'Sanction Letter', 2, 96.1, false, 'HDB-2026-001245', '10:42:01', [
    field('Sanctioned Amount', 100000, 96.5, 'doc-001245-sanction', 1),
    field('Tenure', '36 months', 95.8, 'doc-001245-sanction', 1),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:42:04', '10:42:06', 98.8), step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:42:06', '10:42:11', 96.1), step('Field Extraction', 'COMPLETED', 'Completed', '10:42:11', '10:42:15', 96.1)]),
  base('doc-001245-aadhaarxml', 'Aadhaar_XML.zip', 'Aadhaar XML', 1, 99.4, false, 'HDB-2026-001245', '10:42:01', [
    field('Aadhaar XML', 'Verified', 99.4, 'doc-001245-aadhaarxml', 1),
    field('Name Match', 'Rahul Sharma', 99.1, 'doc-001245-aadhaarxml', 1),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:42:04', '10:42:05', 99.6), step('Field Extraction', 'COMPLETED', 'Completed', '10:42:05', '10:42:08', 99.4)]),
  base('doc-001245-bpi', 'BPI.pdf', 'Miscellaneous', 1, 93.8, false, 'HDB-2026-001245', '10:42:01', [
    field('BPI Account No.', 'XXXXXX7890', 94.1, 'doc-001245-bpi', 1),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:42:04', '10:42:05', 98.2), step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:42:05', '10:42:09', 93.8), step('Field Extraction', 'COMPLETED', 'Completed', '10:42:09', '10:42:12', 93.8)]),
  base('doc-001245-neo', 'NEO_Record.pdf', 'Miscellaneous', 2, 99.2, false, 'HDB-2026-001245', '10:42:01', [
    field('NEO Loan Amount', 90000, 99.2, 'doc-001245-neo', 1),
    field('NEO Account No.', 'XXXXXX7890', 93.6, 'doc-001245-neo', 2),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:42:04', '10:42:06', 99.5), step('Field Extraction', 'COMPLETED', 'Completed', '10:42:06', '10:42:10', 99.2)]),

  // Case 2 — Disbursal Memo discrepancy
  base('doc-001301-memo', 'Disbursal_Memo.pdf', 'Disbursal Memo', 2, 96.3, false, 'HDB-2026-001301', '10:43:01', [
    field('Documented Amount', 132500, 96.3, 'doc-001301-memo', 1),
    field('Expected Net Disbursal', 134500, 98.0, 'doc-001301-neo', 2),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:43:04', '10:43:07', 98.4), step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:43:07', '10:43:13', 96.3), step('Field Extraction', 'COMPLETED', 'Completed', '10:43:13', '10:43:18', 96.3)]),
  base('doc-001301-neo', 'NEO_Record.pdf', 'Miscellaneous', 2, 98.4, false, 'HDB-2026-001301', '10:43:01', [
    field('NEO Loan Amount', 135000, 98.4, 'doc-001301-neo', 1),
    field('Expected Net Disbursal', 134500, 98.0, 'doc-001301-neo', 2),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:43:04', '10:43:06', 99.2), step('Field Extraction', 'COMPLETED', 'Completed', '10:43:06', '10:43:10', 98.4)]),

  // Case 3 — VLM fallback
  base('doc-001310-appform', 'Application_Form.pdf', 'Application Form', 4, 62.3, true, 'HDB-2026-001310', '10:44:01', [
    field('Applicant Name', 'Amit Verma', 62.3, 'doc-001310-appform', 3),
    field('Signature Present', 'Unclear', 60.5, 'doc-001310-appform', 4),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:44:04', '10:44:08', 98.2), step('PaddleOCR', 'WARNING', 'Low confidence on page 3', '10:44:08', '10:44:14', 62.3), step('VLM Fallback', 'COMPLETED', 'VLM invoked for page 3 / Applicant Name', '10:44:14', '10:44:19', 61.4), step('Field Extraction', 'COMPLETED', 'Completed', '10:44:19', '10:44:27', 61.4)]),

  // Case 5 — Top-up
  base('doc-001388-memo', 'Disbursal_Memo.pdf', 'Disbursal Memo', 2, 94.7, false, 'HDB-2026-001388', '10:41:01', [
    field('Documented Amount', 178000, 94.7, 'doc-001388-memo', 1),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:41:04', '10:41:07', 98.6), step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:41:07', '10:41:12', 94.7), step('Field Extraction', 'COMPLETED', 'Completed', '10:41:12', '10:41:16', 94.7)]),
  base('doc-001388-bt', 'BT_Details.pdf', 'BT Details', 2, 93.9, false, 'HDB-2026-001388', '10:41:01', [
    field('Previous Lender', 'Bajaj Finance', 94.8, 'doc-001388-bt', 1),
    field('Outstanding Amount', 45000, 93.9, 'doc-001388-bt', 1),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:41:04', '10:41:07', 98.4), step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:41:07', '10:41:12', 93.9), step('Field Extraction', 'COMPLETED', 'Completed', '10:41:12', '10:41:16', 93.9)]),

  // Case 6 — multiple discrepancies
  base('doc-001401-appform', 'Application_Form.pdf', 'Application Form', 4, 65.0, true, 'HDB-2026-001401', '10:46:01', [
    field('Applicant Name', 'Deepak Nair', 65.0, 'doc-001401-appform', 3),
    field('Loan Amount', 225000, 96.5, 'doc-001401-appform', 2),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:46:04', '10:46:08', 97.9), step('PaddleOCR', 'WARNING', 'Low confidence on page 3', '10:46:08', '10:46:14', 64.1), step('VLM Fallback', 'COMPLETED', 'VLM invoked for page 3', '10:46:14', '10:46:20', 64.1), step('Field Extraction', 'COMPLETED', 'Completed', '10:46:20', '10:46:29', 64.1)]),
  base('doc-001401-memo', 'Disbursal_Memo.pdf', 'Disbursal Memo', 2, 95.4, false, 'HDB-2026-001401', '10:46:01', [
    field('Documented Amount', 218000, 95.4, 'doc-001401-memo', 1),
  ], [step('Docling', 'COMPLETED', 'Parsed', '10:46:04', '10:46:07', 98.0), step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:46:07', '10:46:13', 95.4), step('Field Extraction', 'COMPLETED', 'Completed', '10:46:13', '10:46:18', 95.4)]),
];
