import type { Case } from '@/types';
import { cp, ev, field, step, inr } from './helpers';

const D = {
  appForm: 'doc-001301-appform',
  pan: 'doc-001301-pan',
  aadhaar: 'doc-001301-aadhaar',
  kyc: 'doc-001301-kyc',
  selfie: 'doc-001301-selfie',
  agreement: 'doc-001301-agreement',
  kfs: 'doc-001301-kfs',
  sanction: 'doc-001301-sanction',
  aadhaarXml: 'doc-001301-aadhaarxml',
  bpi: 'doc-001301-bpi',
  memo: 'doc-001301-memo',
  neo: 'doc-001301-neo',
};

const checkpoints = [
  cp(
    1,
    'Loan Amount',
    'VERIFIED',
    98.2,
    'Application Form and NEO loan amount match 90% of Total Loan Amount.',
    'Application Form & NEO Loan Amount must match 90% of Total Loan Amount.',
    [
      field('Total Loan Amount', 150000, 98.5, D.appForm, 2),
      field('Expected 90%', 135000, 100, D.neo, 0),
      field('Application Form Loan Amount', 135000, 98.0, D.appForm, 2),
      field('NEO Loan Amount', 135000, 98.4, D.neo, 1),
    ],
    [
      ev('Application Form — Loan Amount', D.appForm, 'Application_Form.pdf', 2, 'Loan Amount'),
      ev('NEO — Loan Amount field', D.neo, 'NEO_Record.pdf', 1, 'Loan Amount'),
    ],
    { left: inr(135000), right: inr(135000), result: 'MATCH' },
  ),
  cp(2, 'Loan Validity', 'VERIFIED', 99.0, 'Loan validity within permitted window.', 'Loan validity must be within 180 days of login date.', [
    field('Login Date', '2026-08-10', 99.2, D.appForm, 1),
    field('Validity End Date', '2026-11-08', 98.8, D.sanction, 1),
  ], [ev('Sanction Letter — Validity', D.sanction, 'Sanction_Letter.pdf', 1)], { left: '90 days', right: '≤ 180 days', result: 'MATCH' }),
  cp(3, 'Application Form', 'VERIFIED', 97.1, 'Application form complete and signed.', 'Application Form must be complete and signed.', [
    field('Applicant Name', 'Priya Iyer', 97.8, D.appForm, 1),
    field('Signature Present', 'Yes', 96.5, D.appForm, 4),
  ], [ev('Application Form — Signature', D.appForm, 'Application_Form.pdf', 4, 'Signature')], { left: 'Complete + Signed', right: 'Required', result: 'MATCH' }),
  cp(4, 'KYC', 'VERIFIED', 96.5, 'PAN and Aadhaar present and name matches.', 'PAN and Aadhaar must be present and name must match.', [
    field('PAN', 'XXXXX5678Y', 97.0, D.pan, 1),
    field('Aadhaar', 'XXXX XXXX 5678', 96.1, D.aadhaar, 1),
    field('Name Match', 'Priya Iyer', 96.4, D.kyc, 1),
  ], [ev('PAN Card', D.pan, 'PAN.pdf', 1, 'PAN Number'), ev('Aadhaar Card', D.aadhaar, 'Aadhaar.pdf', 1, 'Aadhaar Number')], { left: 'Priya Iyer', right: 'Priya Iyer', result: 'MATCH' }),
  cp(5, 'Selfie / Live Photo', 'VERIFIED', 98.7, 'Live photograph detected with sufficient quality.', 'Live photograph must be present and of sufficient quality.', [field('Live Photo', 'Detected', 98.7, D.selfie, 1)], [ev('Selfie — Live Photo', D.selfie, 'Selfie.jpg', 1)], { left: 'Detected', right: 'Required', result: 'MATCH' }),
  cp(6, 'Loan Agreement', 'VERIFIED', 96.9, 'Loan agreement signed and complete.', 'Loan Agreement must be signed and complete.', [
    field('Signed', 'Yes', 97.2, D.agreement, 3),
    field('Agreement Date', '2026-08-11', 96.6, D.agreement, 1),
  ], [ev('Loan Agreement — Signature', D.agreement, 'Loan_Agreement.pdf', 3, 'Signature')], { left: 'Signed', right: 'Required', result: 'MATCH' }),
  cp(7, 'KFS', 'VERIFIED', 95.4, 'KFS present with correct interest rate.', 'KFS must be present and match sanctioned terms.', [
    field('Interest Rate', '13.2%', 95.8, D.kfs, 1),
    field('Processing Fee', '₹1,500', 95.0, D.kfs, 2),
  ], [ev('KFS — Interest Rate', D.kfs, 'KFS.pdf', 1, 'Interest Rate')], { left: '13.2%', right: '13.2%', result: 'MATCH' }),
  cp(8, 'Sanction Letter', 'VERIFIED', 95.9, 'Sanction letter matches approved amount.', 'Sanction Letter amount must match approved loan amount.', [
    field('Sanctioned Amount', 150000, 96.3, D.sanction, 1),
    field('Tenure', '48 months', 95.5, D.sanction, 1),
  ], [ev('Sanction Letter — Amount', D.sanction, 'Sanction_Letter.pdf', 1, 'Loan Amount')], { left: inr(150000), right: inr(150000), result: 'MATCH' }),
  cp(9, 'Aadhaar XML', 'VERIFIED', 99.2, 'Aadhaar XML verified.', 'Aadhaar XML must be present and match Aadhaar details.', [
    field('Aadhaar XML', 'Verified', 99.2, D.aadhaarXml, 1),
    field('Name Match', 'Priya Iyer', 98.9, D.aadhaarXml, 1),
  ], [ev('Aadhaar XML — Name', D.aadhaarXml, 'Aadhaar_XML.zip', 1, 'Name')], { left: 'Verified', right: 'Required', result: 'MATCH' }),
  cp(10, 'BPI', 'VERIFIED', 93.5, 'BPI matches disbursal account.', 'BPI account number must match NEO disbursal account.', [
    field('BPI Account No.', 'XXXXXX4521', 93.9, D.bpi, 1),
    field('NEO Account No.', 'XXXXXX4521', 93.2, D.neo, 2),
  ], [ev('BPI — Account Number', D.bpi, 'BPI.pdf', 1, 'Account Number'), ev('NEO — Disbursal Account', D.neo, 'NEO_Record.pdf', 2, 'Account Number')], { left: 'XXXXXX4521', right: 'XXXXXX4521', result: 'MATCH' }),
  cp(
    11,
    'Disbursal Memo',
    'DISCREPANCY',
    97.1,
    'Documented net disbursal amount does not match expected net disbursal.',
    'Expected Net Disbursal must match Documented Disbursal Memo amount.',
    [
      field('Expected Net Disbursal', 134500, 98.0, D.neo, 2),
      field('Documented Amount', 132500, 96.3, D.memo, 1),
    ],
    [
      ev('NEO — Net Disbursal', D.neo, 'NEO_Record.pdf', 2, 'Net Disbursal'),
      ev('Disbursal Memo — Amount', D.memo, 'Disbursal_Memo.pdf', 1, 'Net Disbursal'),
    ],
    { left: inr(134500), right: inr(132500), result: 'MISMATCH' },
  ),
  cp(12, 'BT Details', 'NOT_APPLICABLE', 0, 'Not applicable — not a BT case.', 'BT Details required only for balance transfer cases.', [], []),
];

export const case2: Case = {
  id: 'HDB-2026-001301',
  applicant: 'Priya Iyer',
  applicationId: 'APP-2026-881301',
  loanType: 'Personal Loan',
  loanAmount: 150000,
  disbursalAmount: 132500,
  loginDate: '2026-08-10',
  disbursalDate: null,
  documentCount: 11,
  processingTime: '4m 08s',
  processingTimeSeconds: 248,
  dgclScore: 88.7,
  verifiedCount: 10,
  discrepancyCount: 1,
  reviewCount: 1,
  status: 'DISCREPANCY',
  riskLevel: 'HIGH',
  lastUpdated: '2026-08-31 10:43:51',
  checkpoints,
  documentIds: [D.appForm, D.pan, D.aadhaar, D.kyc, D.selfie, D.agreement, D.kfs, D.sanction, D.aadhaarXml, D.bpi, D.memo, D.neo],
  processingSteps: [
    step('System', 'COMPLETED', 'Case received', '10:43:01', '10:43:01'),
    step('System', 'COMPLETED', 'Document classification completed', '10:43:03', '10:43:04'),
    step('Docling', 'COMPLETED', 'Document parsed', '10:43:04', '10:43:08', 98.5),
    step('PaddleOCR', 'COMPLETED', 'OCR completed', '10:43:08', '10:43:16', 96.9),
    step('Field Extraction', 'COMPLETED', 'Field extraction completed', '10:43:16', '10:43:29', 96.1),
    step('Validation', 'COMPLETED', 'DGCL validation completed', '10:43:29', '10:43:49', 88.7),
    step('DGCL Engine', 'COMPLETED', 'Discrepancy detected on Disbursal Memo', '10:43:49', '10:43:51', 97.1),
  ],
};
