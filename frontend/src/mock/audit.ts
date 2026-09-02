import type { AuditEvent } from '@/types';

export const auditEvents: AuditEvent[] = [
  // Case 1
  { id: 'a1', timestamp: '10:42:01', action: 'Case received', component: 'System', result: 'INFO', caseId: 'HDB-2026-001245' },
  { id: 'a2', timestamp: '10:42:03', action: 'Document classification completed', component: 'System', result: 'SUCCESS', caseId: 'HDB-2026-001245', detail: '10 documents classified' },
  { id: 'a3', timestamp: '10:42:07', action: 'Docling parsing completed', component: 'Docling', result: 'SUCCESS', caseId: 'HDB-2026-001245', confidence: 99.1 },
  { id: 'a4', timestamp: '10:42:14', action: 'PaddleOCR completed', component: 'PaddleOCR', result: 'SUCCESS', caseId: 'HDB-2026-001245', confidence: 97.8 },
  { id: 'a5', timestamp: '10:42:27', action: 'Field extraction completed', component: 'Field Extraction', result: 'SUCCESS', caseId: 'HDB-2026-001245', confidence: 97.2 },
  { id: 'a6', timestamp: '10:42:29', action: 'DGCL validation completed', component: 'Validation', result: 'SUCCESS', caseId: 'HDB-2026-001245', confidence: 96.4 },
  { id: 'a7', timestamp: '10:42:31', action: 'Scorecard generated', component: 'DGCL Engine', result: 'SUCCESS', caseId: 'HDB-2026-001245', confidence: 96.4, detail: 'Status: VERIFIED' },

  // Case 3 — VLM fallback
  { id: 'a8', timestamp: '10:44:01', action: 'Case received', component: 'System', result: 'INFO', caseId: 'HDB-2026-001310' },
  { id: 'a9', timestamp: '10:44:03', action: 'Document classification completed', component: 'System', result: 'SUCCESS', caseId: 'HDB-2026-001310' },
  { id: 'a10', timestamp: '10:44:08', action: 'Docling parsing completed', component: 'Docling', result: 'SUCCESS', caseId: 'HDB-2026-001310', confidence: 98.2 },
  { id: 'a11', timestamp: '10:44:14', action: 'Low confidence detected on page 3', component: 'PaddleOCR', result: 'WARNING', caseId: 'HDB-2026-001310', confidence: 62.3 },
  { id: 'a12', timestamp: '10:44:19', action: 'VLM fallback invoked', component: 'VLM Fallback', result: 'WARNING', caseId: 'HDB-2026-001310', confidence: 61.4, detail: 'Page 3 / Applicant Name' },
  { id: 'a13', timestamp: '10:44:27', action: 'Field extraction completed', component: 'Field Extraction', result: 'SUCCESS', caseId: 'HDB-2026-001310', confidence: 61.4 },
  { id: 'a14', timestamp: '10:44:30', action: 'DGCL validation completed', component: 'Validation', result: 'WARNING', caseId: 'HDB-2026-001310', confidence: 84.2 },
  { id: 'a15', timestamp: '10:44:12', action: 'Indeterminate result — sent to review', component: 'DGCL Engine', result: 'WARNING', caseId: 'HDB-2026-001310', confidence: 61.4 },

  // Case 2 — discrepancy
  { id: 'a16', timestamp: '10:43:01', action: 'Case received', component: 'System', result: 'INFO', caseId: 'HDB-2026-001301' },
  { id: 'a17', timestamp: '10:43:16', action: 'PaddleOCR completed', component: 'PaddleOCR', result: 'SUCCESS', caseId: 'HDB-2026-001301', confidence: 96.9 },
  { id: 'a18', timestamp: '10:43:49', action: 'DGCL validation completed', component: 'Validation', result: 'FAILED', caseId: 'HDB-2026-001301', confidence: 88.7 },
  { id: 'a19', timestamp: '10:43:51', action: 'Discrepancy detected on Disbursal Memo', component: 'DGCL Engine', result: 'FAILED', caseId: 'HDB-2026-001301', confidence: 97.1, detail: '₹134,500 ≠ ₹132,500' },

  // Case 4 — missing document
  { id: 'a20', timestamp: '10:45:01', action: 'Case received', component: 'System', result: 'INFO', caseId: 'HDB-2026-001322' },
  { id: 'a21', timestamp: '10:45:30', action: 'Missing document detected: Aadhaar XML', component: 'Validation', result: 'WARNING', caseId: 'HDB-2026-001322' },
  { id: 'a22', timestamp: '10:45:02', action: 'Indeterminate result — sent to review', component: 'DGCL Engine', result: 'WARNING', caseId: 'HDB-2026-001322' },

  // Case 6 — multiple discrepancies
  { id: 'a23', timestamp: '10:46:01', action: 'Case received', component: 'System', result: 'INFO', caseId: 'HDB-2026-001401' },
  { id: 'a24', timestamp: '10:46:14', action: 'Low confidence on page 3', component: 'PaddleOCR', result: 'WARNING', caseId: 'HDB-2026-001401', confidence: 64.1 },
  { id: 'a25', timestamp: '10:46:20', action: 'VLM fallback invoked', component: 'VLM Fallback', result: 'WARNING', caseId: 'HDB-2026-001401', confidence: 64.1 },
  { id: 'a26', timestamp: '10:46:31', action: 'DGCL validation completed — 4 discrepancies', component: 'Validation', result: 'FAILED', caseId: 'HDB-2026-001401', confidence: 79.4 },
  { id: 'a27', timestamp: '10:46:33', action: 'Discrepancies detected — sent to review', component: 'DGCL Engine', result: 'FAILED', caseId: 'HDB-2026-001401', confidence: 79.4 },

  // Human review actions
  { id: 'a28', timestamp: '11:02:14', action: 'Review item assigned to S. Kulkarni', component: 'System', result: 'INFO', caseId: 'HDB-2026-001322', detail: 'Aadhaar XML missing' },
  { id: 'a29', timestamp: '11:08:47', action: 'Operator confirmed extracted value', component: 'System', result: 'INFO', caseId: 'HDB-2026-001301', detail: 'Disbursal Memo — confirmed discrepancy' },
];
