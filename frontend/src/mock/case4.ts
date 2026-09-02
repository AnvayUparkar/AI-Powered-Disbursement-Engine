import type { Case } from '@/types';
import { cp, ev, field, step, inr } from './helpers';

const D = {
  appForm: 'doc-001322-appform',
  pan: 'doc-001322-pan',
  aadhaar: 'doc-001322-aadhaar',
  kyc: 'doc-001322-kyc',
  selfie: 'doc-001322-selfie',
  agreement: 'doc-001322-agreement',
  kfs: 'doc-001322-kfs',
  sanction: 'doc-001322-sanction',
  bpi: 'doc-001322-bpi',
  neo: 'doc-001322-neo',
};

const checkpoints = [
  cp(1, 'Loan Amount', 'VERIFIED', 98.0, 'Loan amount matches 90% threshold.', 'Application Form & NEO Loan Amount must match 90% of Total Loan Amount.', [
    field('Total Loan Amount', 120000, 98.4, D.appForm, 2),
    field('Expected 90%', 108000, 100, D.neo, 0),
    field('Application Form Loan Amount', 108000, 97.7, D.appForm, 2),
    field('NEO Loan Amount', 108000, 98.1, D.neo, 1),
  ], [ev('Application Form — Loan Amount', D.appForm, 'Application_Form.pdf', 2, 'Loan Amount'), ev('NEO — Loan Amount', D.neo, 'NEO_Record.pdf', 1, 'Loan Amount')], { left: inr(108000), right: inr(108000), result: 'MATCH' }),
  cp(2, 'Loan Validity', 'VERIFIED', 99.1, 'Loan validity within permitted window.', 'Loan validity must be within 180 days of login date.', [
    field('Login Date', '2026-08-11', 99.3, D.appForm, 1),
    field('Validity End Date', '2026-11-09', 98.9, D.sanction, 1),
  ], [ev('Sanction Letter — Validity', D.sanction, 'Sanction_Letter.pdf', 1)], { left: '90 days', right: '≤ 180 days', result: 'MATCH' }),
  cp(3, 'Application Form', 'VERIFIED', 97.3, 'Application form complete and signed.', 'Application Form must be complete and signed.', [
    field('Applicant Name', 'Sneha Reddy', 97.9, D.appForm, 1),
    field('Signature Present', 'Yes', 96.8, D.appForm, 4),
  ], [ev('Application Form — Signature', D.appForm, 'Application_Form.pdf', 4, 'Signature')], { left: 'Complete + Signed', right: 'Required', result: 'MATCH' }),
  cp(4, 'KYC', 'VERIFIED', 96.2, 'PAN and Aadhaar present and name matches.', 'PAN and Aadhaar must be present and name must match.', [
    field('PAN', 'XXXXX3456P', 96.7, D.pan, 1),
    field('Aadhaar', 'XXXX XXXX 3456', 95.8, D.aadhaar, 1),
    field('Name Match', 'Sneha Reddy', 96.1, D.kyc, 1),
  ], [ev('PAN Card', D.pan, 'PAN.pdf', 1, 'PAN Number'), ev('Aadhaar Card', D.aadhaar, 'Aadhaar.pdf', 1, 'Aadhaar Number')], { left: 'Sneha Reddy', right: 'Sneha Reddy', result: 'MATCH' }),
  cp(5, 'Selfie / Live Photo', 'VERIFIED', 98.5, 'Live photograph detected with sufficient quality.', 'Live photograph must be present and of sufficient quality.', [field('Live Photo', 'Detected', 98.5, D.selfie, 1)], [ev('Selfie — Live Photo', D.selfie, 'Selfie.jpg', 1)], { left: 'Detected', right: 'Required', result: 'MATCH' }),
  cp(6, 'Loan Agreement', 'VERIFIED', 96.7, 'Loan agreement signed and complete.', 'Loan Agreement must be signed and complete.', [
    field('Signed', 'Yes', 97.0, D.agreement, 3),
    field('Agreement Date', '2026-08-12', 96.4, D.agreement, 1),
  ], [ev('Loan Agreement — Signature', D.agreement, 'Loan_Agreement.pdf', 3, 'Signature')], { left: 'Signed', right: 'Required', result: 'MATCH' }),
  cp(7, 'KFS', 'VERIFIED', 95.2, 'KFS present with correct interest rate.', 'KFS must be present and match sanctioned terms.', [
    field('Interest Rate', '12.4%', 95.6, D.kfs, 1),
    field('Processing Fee', '₹1,200', 94.8, D.kfs, 2),
  ], [ev('KFS — Interest Rate', D.kfs, 'KFS.pdf', 1, 'Interest Rate')], { left: '12.4%', right: '12.4%', result: 'MATCH' }),
  cp(8, 'Sanction Letter', 'VERIFIED', 95.8, 'Sanction letter matches approved amount.', 'Sanction Letter amount must match approved loan amount.', [
    field('Sanctioned Amount', 120000, 96.2, D.sanction, 1),
    field('Tenure', '36 months', 95.4, D.sanction, 1),
  ], [ev('Sanction Letter — Amount', D.sanction, 'Sanction_Letter.pdf', 1, 'Loan Amount')], { left: inr(120000), right: inr(120000), result: 'MATCH' }),
  cp(
    9,
    'Aadhaar XML',
    'INDETERMINATE',
    0,
    'Aadhaar XML document is missing from the case file.',
    'Aadhaar XML must be present and match Aadhaar details.',
    [],
    [],
  ),
  cp(10, 'BPI', 'VERIFIED', 93.3, 'BPI matches disbursal account.', 'BPI account number must match NEO disbursal account.', [
    field('BPI Account No.', 'XXXXXX7712', 93.7, D.bpi, 1),
    field('NEO Account No.', 'XXXXXX7712', 93.0, D.neo, 2),
  ], [ev('BPI — Account Number', D.bpi, 'BPI.pdf', 1, 'Account Number'), ev('NEO — Disbursal Account', D.neo, 'NEO_Record.pdf', 2, 'Account Number')], { left: 'XXXXXX7712', right: 'XXXXXX7712', result: 'MATCH' }),
  cp(11, 'Disbursal Memo', 'NOT_APPLICABLE', 0, 'Not applicable for this loan type.', 'Disbursal Memo required only for top-up / BT cases.', [], []),
  cp(12, 'BT Details', 'NOT_APPLICABLE', 0, 'Not applicable — not a BT case.', 'BT Details required only for balance transfer cases.', [], []),
];

export const case4: Case = {
  id: 'HDB-2026-001322',
  applicant: 'Sneha Reddy',
  applicationId: 'APP-2026-881322',
  loanType: 'Personal Loan',
  loanAmount: 120000,
  disbursalAmount: 108000,
  loginDate: '2026-08-11',
  disbursalDate: null,
  documentCount: 9,
  processingTime: '3m 48s',
  processingTimeSeconds: 228,
  dgclScore: 86.1,
  verifiedCount: 8,
  discrepancyCount: 0,
  reviewCount: 1,
  status: 'INDETERMINATE',
  riskLevel: 'MEDIUM',
  lastUpdated: '2026-08-31 10:45:02',
  checkpoints,
  documentIds: [D.appForm, D.pan, D.aadhaar, D.kyc, D.selfie, D.agreement, D.kfs, D.sanction, D.bpi, D.neo],
  processingSteps: [
    step('System', 'COMPLETED', 'Case received', '10:45:01', '10:45:01'),
    step('System', 'COMPLETED', 'Document classification completed', '10:45:03', '10:45:04'),
    step('Docling', 'COMPLETED', 'Document parsed', '10:45:04', '10:45:08', 98.0),
    step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:45:08', '10:45:15', 97.1),
    step('Field Extraction', 'COMPLETED', 'Field extraction completed', '10:45:15', '10:45:27', 96.5),
    step('Validation', 'WARNING', 'Missing document detected: Aadhaar XML', '10:45:27', '10:45:30', 0),
    step('DGCL Engine', 'COMPLETED', 'Indeterminate result — sent to review', '10:45:30', '10:45:02', 0),
  ],
};
