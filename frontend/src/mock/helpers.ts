import type {
  BoundingBox,
  Checkpoint,
  Evidence,
  ExtractedField,
  ProcessingStep,
} from '@/types';

export const DGCL_CHECKPOINT_NAMES = [
  'Loan Amount',
  'Loan Validity',
  'Application Form',
  'KYC',
  'Selfie / Live Photo',
  'Loan Agreement',
  'KFS',
  'Sanction Letter',
  'Aadhaar XML',
  'BPI',
  'Disbursal Memo',
  'BT Details',
] as const;

let idc = 0;
export const uid = (prefix: string) => `${prefix}-${++idc}`;

export const ev = (
  label: string,
  documentId: string,
  documentName: string,
  page: number,
  field?: string,
  boundingBox?: BoundingBox,
): Evidence => ({
  id: uid('ev'),
  label,
  documentId,
  documentName,
  page,
  field,
  boundingBox,
});

export const field = (
  name: string,
  value: string | number | null,
  confidence: number,
  sourceDocumentId: string,
  page?: number,
  evidence: Evidence[] = [],
): ExtractedField => ({
  id: uid('fld'),
  name,
  value,
  confidence,
  sourceDocumentId,
  page,
  evidence,
});

export const cp = (
  id: number,
  name: string,
  status: Checkpoint['status'],
  confidence: number,
  reason: string,
  rule: string,
  extractedFields: ExtractedField[] = [],
  evidence: Evidence[] = [],
  validation?: Checkpoint['validation'],
): Checkpoint => ({
  id,
  name,
  status,
  confidence,
  reason,
  rule,
  extractedFields,
  evidence,
  validation,
});

export const step = (
  component: ProcessingStep['component'],
  status: ProcessingStep['status'],
  detail: string,
  startedAt: string,
  completedAt?: string,
  confidence?: number,
): ProcessingStep => ({
  id: uid('stp'),
  component,
  status,
  detail,
  startedAt,
  completedAt,
  confidence,
});

export const inr = (n: number) =>
  '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 });

export const pct = (n: number) => `${n.toFixed(1)}%`;

export const maskAadhaar = (a: string) => {
  const digits = a.replace(/\s/g, '');
  return `XXXX XXXX ${digits.slice(-4)}`;
};

export const maskPan = (p: string) => {
  if (p.length <= 5) return p;
  return `${'X'.repeat(p.length - 4)}${p.slice(-4)}`;
};
