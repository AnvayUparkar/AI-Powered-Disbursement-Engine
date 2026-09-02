import type { Case } from '@/types';
import { cp, ev, field, step, inr } from './helpers';

const D = {
  appForm: 'doc-001310-appform',
  pan: 'doc-001310-pan',
  aadhaar: 'doc-001310-aadhaar',
  kyc: 'doc-001310-kyc',
  selfie: 'doc-001310-selfie',
  agreement: 'doc-001310-agreement',
  kfs: 'doc-001310-kfs',
  sanction: 'doc-001310-sanction',
  aadhaarXml: 'doc-001310-aadhaarxml',
  bpi: 'doc-001310-bpi',
  neo: 'doc-001310-neo',
};

const checkpoints = [
  cp(1, 'Loan Amount', 'VERIFIED', 97.4, 'Loan amount matches 90% threshold.', 'Application Form & NEO Loan Amount must match 90% of Total Loan Amount.', [
    field('Total Loan Amount', 75000, 97.8, D.appForm, 2),
    field('Expected 90%', 67500, 100, D.neo, 0),
    field('Application Form Loan Amount', 67500, 97.1, D.appForm, 2),
    field('NEO Loan Amount', 67500, 97.5, D.neo, 1),
  ], [ev('Application Form — Loan Amount', D.appForm, 'Application_Form.pdf', 2, 'Loan Amount'), ev('NEO — Loan Amount', D.neo, 'NEO_Record.pdf', 1, 'Loan Amount')], { left: inr(67500), right: inr(67500), result: 'MATCH' }),
  cp(2, 'Loan Validity', 'VERIFIED', 98.8, 'Loan validity within permitted window.', 'Loan validity must be within 180 days of login date.', [
    field('Login Date', '2026-08-09', 99.0, D.appForm, 1),
    field('Validity End Date', '2026-11-07', 98.6, D.sanction, 1),
  ], [ev('Sanction Letter — Validity', D.sanction, 'Sanction_Letter.pdf', 1)], { left: '90 days', right: '≤ 180 days', result: 'MATCH' }),
  cp(
    3,
    'Application Form',
    'INDETERMINATE',
    61.4,
    'Live photograph detected, but image quality is insufficient for reliable verification.',
    'Application Form must be complete and signed.',
    [
      field('Applicant Name', 'Amit Verma', 62.3, D.appForm, 3),
      field('Signature Present', 'Unclear', 60.5, D.appForm, 4),
    ],
    [ev('Application Form — Applicant Name (low confidence)', D.appForm, 'Application_Form.pdf', 3, 'Applicant Name')],
    { left: 'Unclear', right: 'Required', result: 'INCONCLUSIVE' },
  ),
  cp(4, 'KYC', 'VERIFIED', 95.9, 'PAN and Aadhaar present and name matches.', 'PAN and Aadhaar must be present and name must match.', [
    field('PAN', 'XXXXX9012Z', 96.4, D.pan, 1),
    field('Aadhaar', 'XXXX XXXX 9012', 95.5, D.aadhaar, 1),
    field('Name Match', 'Amit Verma', 95.8, D.kyc, 1),
  ], [ev('PAN Card', D.pan, 'PAN.pdf', 1, 'PAN Number'), ev('Aadhaar Card', D.aadhaar, 'Aadhaar.pdf', 1, 'Aadhaar Number')], { left: 'Amit Verma', right: 'Amit Verma', result: 'MATCH' }),
  cp(5, 'Selfie / Live Photo', 'VERIFIED', 97.5, 'Live photograph detected with sufficient quality.', 'Live photograph must be present and of sufficient quality.', [field('Live Photo', 'Detected', 97.5, D.selfie, 1)], [ev('Selfie — Live Photo', D.selfie, 'Selfie.jpg', 1)], { left: 'Detected', right: 'Required', result: 'MATCH' }),
  cp(6, 'Loan Agreement', 'VERIFIED', 96.4, 'Loan agreement signed and complete.', 'Loan Agreement must be signed and complete.', [
    field('Signed', 'Yes', 96.8, D.agreement, 3),
    field('Agreement Date', '2026-08-10', 96.0, D.agreement, 1),
  ], [ev('Loan Agreement — Signature', D.agreement, 'Loan_Agreement.pdf', 3, 'Signature')], { left: 'Signed', right: 'Required', result: 'MATCH' }),
  cp(7, 'KFS', 'VERIFIED', 95.0, 'KFS present with correct interest rate.', 'KFS must be present and match sanctioned terms.', [
    field('Interest Rate', '15.8%', 95.4, D.kfs, 1),
    field('Processing Fee', '₹750', 94.6, D.kfs, 2),
  ], [ev('KFS — Interest Rate', D.kfs, 'KFS.pdf', 1, 'Interest Rate')], { left: '15.8%', right: '15.8%', result: 'MATCH' }),
  cp(8, 'Sanction Letter', 'VERIFIED', 95.6, 'Sanction letter matches approved amount.', 'Sanction Letter amount must match approved loan amount.', [
    field('Sanctioned Amount', 75000, 96.0, D.sanction, 1),
    field('Tenure', '24 months', 95.2, D.sanction, 1),
  ], [ev('Sanction Letter — Amount', D.sanction, 'Sanction_Letter.pdf', 1, 'Loan Amount')], { left: inr(75000), right: inr(75000), result: 'MATCH' }),
  cp(9, 'Aadhaar XML', 'VERIFIED', 98.9, 'Aadhaar XML verified.', 'Aadhaar XML must be present and match Aadhaar details.', [
    field('Aadhaar XML', 'Verified', 98.9, D.aadhaarXml, 1),
    field('Name Match', 'Amit Verma', 98.6, D.aadhaarXml, 1),
  ], [ev('Aadhaar XML — Name', D.aadhaarXml, 'Aadhaar_XML.zip', 1, 'Name')], { left: 'Verified', right: 'Required', result: 'MATCH' }),
  cp(10, 'BPI', 'VERIFIED', 93.1, 'BPI matches disbursal account.', 'BPI account number must match NEO disbursal account.', [
    field('BPI Account No.', 'XXXXXX3378', 93.5, D.bpi, 1),
    field('NEO Account No.', 'XXXXXX3378', 92.8, D.neo, 2),
  ], [ev('BPI — Account Number', D.bpi, 'BPI.pdf', 1, 'Account Number'), ev('NEO — Disbursal Account', D.neo, 'NEO_Record.pdf', 2, 'Account Number')], { left: 'XXXXXX3378', right: 'XXXXXX3378', result: 'MATCH' }),
  cp(11, 'Disbursal Memo', 'NOT_APPLICABLE', 0, 'Not applicable for this loan type.', 'Disbursal Memo required only for top-up / BT cases.', [], []),
  cp(12, 'BT Details', 'NOT_APPLICABLE', 0, 'Not applicable — not a BT case.', 'BT Details required only for balance transfer cases.', [], []),
];

export const case3: Case = {
  id: 'HDB-2026-001310',
  applicant: 'Amit Verma',
  applicationId: 'APP-2026-881310',
  loanType: 'Personal Loan',
  loanAmount: 75000,
  disbursalAmount: 67500,
  loginDate: '2026-08-09',
  disbursalDate: null,
  documentCount: 10,
  processingTime: '5m 24s',
  processingTimeSeconds: 324,
  dgclScore: 84.2,
  verifiedCount: 9,
  discrepancyCount: 0,
  reviewCount: 1,
  status: 'INDETERMINATE',
  riskLevel: 'HIGH',
  lastUpdated: '2026-08-31 10:44:12',
  checkpoints,
  documentIds: [D.appForm, D.pan, D.aadhaar, D.kyc, D.selfie, D.agreement, D.kfs, D.sanction, D.aadhaarXml, D.bpi, D.neo],
  processingSteps: [
    step('System', 'COMPLETED', 'Case received', '10:44:01', '10:44:01'),
    step('System', 'COMPLETED', 'Document classification completed', '10:44:03', '10:44:04'),
    step('Docling', 'COMPLETED', 'Document parsed', '10:44:04', '10:44:08', 98.2),
    step('PaddleOCR', 'WARNING', 'Low confidence detected on page 3', '10:44:08', '10:44:14', 62.3),
    step('VLM Fallback', 'COMPLETED', 'VLM invoked for page 3 / Applicant Name', '10:44:14', '10:44:19', 61.4),
    step('Field Extraction', 'COMPLETED', 'Field extraction completed', '10:44:19', '10:44:27', 61.4),
    step('Validation', 'COMPLETED', 'DGCL validation completed', '10:44:27', '10:44:30', 84.2),
    step('DGCL Engine', 'COMPLETED', 'Indeterminate result — sent to review', '10:44:30', '10:44:12', 61.4),
  ],
};
