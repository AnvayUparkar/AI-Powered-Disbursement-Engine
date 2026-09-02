import type { Case } from '@/types';
import { cp, ev, field, step, inr } from './helpers';

const D = {
  appForm: 'doc-001388-appform',
  pan: 'doc-001388-pan',
  aadhaar: 'doc-001388-aadhaar',
  kyc: 'doc-001388-kyc',
  selfie: 'doc-001388-selfie',
  agreement: 'doc-001388-agreement',
  kfs: 'doc-001388-kfs',
  sanction: 'doc-001388-sanction',
  aadhaarXml: 'doc-001388-aadhaarxml',
  bpi: 'doc-001388-bpi',
  memo: 'doc-001388-memo',
  bt: 'doc-001388-bt',
  neo: 'doc-001388-neo',
};

const checkpoints = [
  cp(1, 'Loan Amount', 'VERIFIED', 98.6, 'Loan amount matches 90% threshold for top-up.', 'Application Form & NEO Loan Amount must match 90% of Total Loan Amount.', [
    field('Total Loan Amount', 200000, 98.9, D.appForm, 2),
    field('Expected 90%', 180000, 100, D.neo, 0),
    field('Application Form Loan Amount', 180000, 98.3, D.appForm, 2),
    field('NEO Loan Amount', 180000, 98.7, D.neo, 1),
  ], [ev('Application Form — Loan Amount', D.appForm, 'Application_Form.pdf', 2, 'Loan Amount'), ev('NEO — Loan Amount', D.neo, 'NEO_Record.pdf', 1, 'Loan Amount')], { left: inr(180000), right: inr(180000), result: 'MATCH' }),
  cp(2, 'Loan Validity', 'VERIFIED', 99.2, 'Loan validity within permitted window.', 'Loan validity must be within 180 days of login date.', [
    field('Login Date', '2026-08-08', 99.4, D.appForm, 1),
    field('Validity End Date', '2026-11-06', 99.0, D.sanction, 1),
  ], [ev('Sanction Letter — Validity', D.sanction, 'Sanction_Letter.pdf', 1)], { left: '90 days', right: '≤ 180 days', result: 'MATCH' }),
  cp(3, 'Application Form', 'VERIFIED', 97.8, 'Application form complete and signed.', 'Application Form must be complete and signed.', [
    field('Applicant Name', 'Karan Mehta', 98.2, D.appForm, 1),
    field('Signature Present', 'Yes', 97.4, D.appForm, 4),
  ], [ev('Application Form — Signature', D.appForm, 'Application_Form.pdf', 4, 'Signature')], { left: 'Complete + Signed', right: 'Required', result: 'MATCH' }),
  cp(4, 'KYC', 'VERIFIED', 96.9, 'PAN and Aadhaar present and name matches.', 'PAN and Aadhaar must be present and name must match.', [
    field('PAN', 'XXXXX7890Q', 97.3, D.pan, 1),
    field('Aadhaar', 'XXXX XXXX 7890', 96.6, D.aadhaar, 1),
    field('Name Match', 'Karan Mehta', 96.8, D.kyc, 1),
  ], [ev('PAN Card', D.pan, 'PAN.pdf', 1, 'PAN Number'), ev('Aadhaar Card', D.aadhaar, 'Aadhaar.pdf', 1, 'Aadhaar Number')], { left: 'Karan Mehta', right: 'Karan Mehta', result: 'MATCH' }),
  cp(5, 'Selfie / Live Photo', 'VERIFIED', 99.0, 'Live photograph detected with sufficient quality.', 'Live photograph must be present and of sufficient quality.', [field('Live Photo', 'Detected', 99.0, D.selfie, 1)], [ev('Selfie — Live Photo', D.selfie, 'Selfie.jpg', 1)], { left: 'Detected', right: 'Required', result: 'MATCH' }),
  cp(6, 'Loan Agreement', 'VERIFIED', 97.4, 'Loan agreement signed and complete.', 'Loan Agreement must be signed and complete.', [
    field('Signed', 'Yes', 97.7, D.agreement, 3),
    field('Agreement Date', '2026-08-09', 97.1, D.agreement, 1),
  ], [ev('Loan Agreement — Signature', D.agreement, 'Loan_Agreement.pdf', 3, 'Signature')], { left: 'Signed', right: 'Required', result: 'MATCH' }),
  cp(7, 'KFS', 'VERIFIED', 96.0, 'KFS present with correct interest rate.', 'KFS must be present and match sanctioned terms.', [
    field('Interest Rate', '11.8%', 96.4, D.kfs, 1),
    field('Processing Fee', '₹2,000', 95.6, D.kfs, 2),
  ], [ev('KFS — Interest Rate', D.kfs, 'KFS.pdf', 1, 'Interest Rate')], { left: '11.8%', right: '11.8%', result: 'MATCH' }),
  cp(8, 'Sanction Letter', 'VERIFIED', 96.5, 'Sanction letter matches approved amount.', 'Sanction Letter amount must match approved loan amount.', [
    field('Sanctioned Amount', 200000, 96.9, D.sanction, 1),
    field('Tenure', '60 months', 96.1, D.sanction, 1),
  ], [ev('Sanction Letter — Amount', D.sanction, 'Sanction_Letter.pdf', 1, 'Loan Amount')], { left: inr(200000), right: inr(200000), result: 'MATCH' }),
  cp(9, 'Aadhaar XML', 'VERIFIED', 99.3, 'Aadhaar XML verified.', 'Aadhaar XML must be present and match Aadhaar details.', [
    field('Aadhaar XML', 'Verified', 99.3, D.aadhaarXml, 1),
    field('Name Match', 'Karan Mehta', 99.0, D.aadhaarXml, 1),
  ], [ev('Aadhaar XML — Name', D.aadhaarXml, 'Aadhaar_XML.zip', 1, 'Name')], { left: 'Verified', right: 'Required', result: 'MATCH' }),
  cp(10, 'BPI', 'VERIFIED', 94.0, 'BPI matches disbursal account.', 'BPI account number must match NEO disbursal account.', [
    field('BPI Account No.', 'XXXXXX2245', 94.4, D.bpi, 1),
    field('NEO Account No.', 'XXXXXX2245', 93.7, D.neo, 2),
  ], [ev('BPI — Account Number', D.bpi, 'BPI.pdf', 1, 'Account Number'), ev('NEO — Disbursal Account', D.neo, 'NEO_Record.pdf', 2, 'Account Number')], { left: 'XXXXXX2245', right: 'XXXXXX2245', result: 'MATCH' }),
  cp(11, 'Disbursal Memo', 'VERIFIED', 95.1, 'Disbursal memo matches expected net disbursal.', 'Expected Net Disbursal must match Documented Disbursal Memo amount.', [
    field('Expected Net Disbursal', 178000, 95.6, D.neo, 2),
    field('Documented Amount', 178000, 94.7, D.memo, 1),
  ], [ev('NEO — Net Disbursal', D.neo, 'NEO_Record.pdf', 2, 'Net Disbursal'), ev('Disbursal Memo — Amount', D.memo, 'Disbursal_Memo.pdf', 1, 'Net Disbursal')], { left: inr(178000), right: inr(178000), result: 'MATCH' }),
  cp(12, 'BT Details', 'VERIFIED', 94.3, 'BT details present and match lender records.', 'BT Details must be present for top-up with balance transfer.', [
    field('Previous Lender', 'Bajaj Finance', 94.8, D.bt, 1),
    field('Outstanding Amount', 45000, 93.9, D.bt, 1),
  ], [ev('BT Details — Previous Lender', D.bt, 'BT_Details.pdf', 1, 'Previous Lender')], { left: inr(45000), right: inr(45000), result: 'MATCH' }),
];

export const case5: Case = {
  id: 'HDB-2026-001388',
  applicant: 'Karan Mehta',
  applicationId: 'APP-2026-881388',
  loanType: 'DGCL Topup',
  loanAmount: 200000,
  disbursalAmount: 178000,
  loginDate: '2026-08-08',
  disbursalDate: '2026-08-13',
  documentCount: 12,
  processingTime: '4m 22s',
  processingTimeSeconds: 262,
  dgclScore: 97.1,
  verifiedCount: 12,
  discrepancyCount: 0,
  reviewCount: 0,
  status: 'VERIFIED',
  riskLevel: 'LOW',
  lastUpdated: '2026-08-31 10:41:55',
  checkpoints,
  documentIds: [D.appForm, D.pan, D.aadhaar, D.kyc, D.selfie, D.agreement, D.kfs, D.sanction, D.aadhaarXml, D.bpi, D.memo, D.bt, D.neo],
  processingSteps: [
    step('System', 'COMPLETED', 'Case received', '10:41:01', '10:41:01'),
    step('System', 'COMPLETED', 'Document classification completed', '10:41:03', '10:41:04'),
    step('Docling', 'COMPLETED', 'Document parsed', '10:41:04', '10:41:08', 99.3),
    step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:41:08', '10:41:16', 98.1),
    step('Field Extraction', 'COMPLETED', 'Field extraction completed', '10:41:16', '10:41:29', 97.4),
    step('Validation', 'COMPLETED', 'DGCL validation completed', '10:41:29', '10:41:32', 97.1),
    step('DGCL Engine', 'COMPLETED', 'Scorecard generated', '10:41:32', '10:41:55', 97.1),
  ],
};
