export type CheckpointStatus =
  | 'VERIFIED'
  | 'DISCREPANCY'
  | 'INDETERMINATE'
  | 'NOT_APPLICABLE'
  | 'PROCESSING';

export type CaseStatus =
  | 'VERIFIED'
  | 'DISCREPANCY'
  | 'INDETERMINATE'
  | 'PROCESSING';

export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH';

export type DocumentType =
  | 'Application Form'
  | 'PAN'
  | 'Aadhaar'
  | 'KYC'
  | 'KFS'
  | 'Sanction Letter'
  | 'Loan Agreement'
  | 'Disbursal Memo'
  | 'BT Details'
  | 'Aadhaar XML'
  | 'VKYC Audit Trail'
  | 'Miscellaneous';

export type OcrStatus = 'COMPLETED' | 'PROCESSING' | 'FAILED' | 'PENDING';
export type ExtractionStatus = 'COMPLETED' | 'PROCESSING' | 'FAILED' | 'PENDING';

export type ProcessingComponent =
  | 'Docling'
  | 'PaddleOCR'
  | 'VLM Fallback'
  | 'Field Extraction'
  | 'Validation'
  | 'DGCL Engine'
  | 'System';

export type ReviewPriority = 'LOW' | 'MEDIUM' | 'HIGH';

export interface Evidence {
  id: string;
  label: string;
  documentId: string;
  documentName: string;
  page: number;
  field?: string;
  boundingBox?: BoundingBox;
}

export interface BoundingBox {
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CandidateMatch {
  bbox: number[];
  page: number;
  text: string;
  confidence: number;
  match_confidence: number;
  match_strategy: string;
}

export interface OCRToken {
  id: string;
  text: string;
  bbox: number[];
  normalized_bbox: number[];
  page: number;
  confidence: number;
  /** RapidOCR's own per-text-cell recognition score. Null when Docling reported none. */
  ocr_confidence?: number | null;
  /** The Docling layout model's per-cluster score for the region this token sits in. */
  layout_confidence?: number | null;
  block_id?: number;
  line_id?: number;
}

/** Docling's per-stage quality report. Null means that stage did not run for this document. */
export interface StageScores {
  layout_score?: number | null;
  ocr_score?: number | null;
  table_score?: number | null;
  parse_score?: number | null;
  quality_grade?: string | null;
}

/** A TableFormer grid cell. These never appear in ocr_tokens: elements inside a detected
 *  table are removed by TableRegionMask, so their text reaches rawText only via the
 *  [TABLE] block. Drawn from this list instead. */
export interface DebugTableCell {
  id?: string | null;
  text: string;
  bbox: number[];
  page_number: number;
  confidence?: number | null;
}

export interface DocumentDebugInfo {
  field_locations?: Record<string, any>;
  ocr_tokens?: OCRToken[];
  ocr_tokens_by_page?: Record<number, OCRToken[]>;
  table_cells?: DebugTableCell[];
  stage_scores?: StageScores;
}

export interface TableCellRecord {
  row_index: number;
  col_index: number;
  row_span?: number;
  col_span?: number;
  text: string;
  is_header?: boolean;
  bbox?: number[];
  confidence?: number;
}

export interface TableCellRecord {
  row_index: number;
  col_index: number;
  row_span?: number;
  col_span?: number;
  text: string;
  is_header?: boolean;
  bbox?: number[];
  confidence?: number;
}

export interface ExtractedField {
  id: string;
  name: string;
  value: string | number | null;
  confidence: number;
  sourceDocumentId: string;
  page?: number;
  evidence?: Evidence[];
  type?: 'key_value' | 'text' | 'heading' | 'table';
  source?: 'ocr' | 'vlm' | 'docling' | 'xml' | 'llm' | 'OPENROUTER_LLM' | string;
  bbox?: number[];
  ocrOriginal?: string;
  headers?: string[];
  rows?: string[][];
  cells?: TableCellRecord[];
  markdown?: string;
  /** Why a field has (or lacks) a bounding box. The last three are NOT location failures:
   *  not_extracted = no value was produced; not_locatable = derived flag that never appears
   *  on a page; no_ocr_text = the document yielded nothing to search. */
  locationStatus?:
    | 'resolved'
    | 'unresolved'
    | 'not_extracted'
    | 'not_locatable'
    | 'no_ocr_text';
  matchedText?: string;
  matchConfidence?: number;
  /** RapidOCR recognition score of the token this field matched. */
  ocrConfidence?: number | null;
  /** Layout model score for the region that token sits in. */
  layoutConfidence?: number | null;
  reason?: string;
  matchStrategy?: string;
  candidates?: CandidateMatch[];
}

export interface Checkpoint {
  id: number;
  name: string;
  status: CheckpointStatus;
  confidence: number;
  reason: string;
  summary?: string;
  rule: string;
  evidence: Evidence[];
  extractedFields: ExtractedField[];
  validation?: {
    left: string;
    right: string;
    result: 'MATCH' | 'MISMATCH' | 'INCONCLUSIVE';
    leftSource?: string;
    rightSource?: string;
  };
  comparisons?: ComparisonResult[];
}

export interface ProcessingStep {
  id: string;
  component: ProcessingComponent;
  status: 'COMPLETED' | 'PROCESSING' | 'FAILED' | 'SKIPPED' | 'WARNING';
  detail: string;
  startedAt: string;
  completedAt?: string;
  confidence?: number;
}

export interface DocumentRecord {
  id: string;
  name: string;
  type: DocumentType;
  pages: number;
  ocrStatus: OcrStatus;
  extractionStatus: ExtractionStatus;
  confidence: number;
  vlmUsed: boolean;
  uploadedAt: string;
  caseId: string;
  sizeKb: number;
  extractedFields: ExtractedField[];
  processingSteps: ProcessingStep[];
  rawText?: string;
  formattedText?: string;
  debug?: DocumentDebugInfo;
}


export interface ComparisonResult {
  check_id: string;
  subnode: string;
  field: string;
  sources: string[];
  values: (string | number | null)[];
  match_type: string;
  match_status: 'MATCH' | 'MISMATCH' | 'REVIEW';
  confidence: number;
  method: string;
  llm_used?: boolean;
  notes?: string | null;
}

export interface Case {
  id: string;
  applicant: string;
  applicationId: string;
  loanType: string;
  loanAmount: number;
  disbursalAmount: number;
  loginDate: string;
  disbursalDate: string | null;
  documentCount: number;
  processingTime: string;
  processingTimeSeconds: number;
  dgclScore: number;
  verifiedCount: number;
  discrepancyCount: number;
  reviewCount: number;
  status: CaseStatus;
  riskLevel: RiskLevel;
  lastUpdated: string;
  checkpoints: Checkpoint[];
  documentIds: string[];
  processingSteps: ProcessingStep[];
  comparisonResults?: ComparisonResult[];
}

export interface ReviewItem {
  id: string;
  caseId: string;
  issue: string;
  checkpointName: string;
  checkpointId: number;
  confidence: number;
  priority: ReviewPriority;
  createdAt: string;
  assignedTo: string | null;
  documentId: string;
  fieldName: string;
  extractedValue: string;
  systemRecommendation: 'REVIEW' | 'DISCREPANCY';
}

export interface AuditEvent {
  id: string;
  timestamp: string;
  action: string;
  component: ProcessingComponent;
  result: 'SUCCESS' | 'WARNING' | 'FAILED' | 'INFO';
  confidence?: number;
  caseId?: string;
  detail?: string;
}

export interface DashboardKpis {
  casesProcessedToday: number;
  documentsProcessed: number;
  verified: number;
  discrepancies: number;
  needsReview: number;
  dgclValidation: number;
  dgclTarget: number;
  avgProcessingSeconds: number;
  avgProcessingTargetSeconds: number;
  docProcessedToday: number;
  ocrSuccessRate: number;
  vlmFallbackRate: number;
  extractionSuccessRate: number;
  avgDocProcessingSeconds: number;
}

export interface CheckpointPerformance {
  id: number;
  name: string;
  passRate: number;
}

export interface ReportSummary {
  totalCases: number;
  verified: number;
  discrepancies: number;
  indeterminate: number;
  avgProcessingSeconds: number;
  vlmFallbackPct: number;
  checkpointPerformance: CheckpointPerformance[];
  discrepancyTrend: { day: string; count: number }[];
  reviewWorkload: { day: string; created: number; resolved: number }[];
  processingLatency: { day: string; seconds: number }[];
  extractionAccuracy: { day: string; pct: number }[];
  vlmFallbackTrend: { day: string; pct: number }[];
}

export interface SystemSettings {
  ocrConfidenceThreshold: number;
  vlmFallbackThreshold: number;
  humanReviewThreshold: number;
  processingMode: 'AUTOMATED' | 'ASSISTED' | 'MANUAL';
  notificationsEnabled: boolean;
  sessionTimeoutMinutes: number;
}

export interface Node2HealthResponse {
  status: string;
  app_name: string;
  environment: string;
  ocr_engine: string;
  vlm_enabled: boolean;
}

export interface Node2ProcessRequest {
  document_id: string;
  s3_key: string;
  s3_bucket?: string;
}

export interface Node2LayoutElement {
  id: string;
  type: string;
  text: string;
  bbox: number[];
  confidence: number;
  page_number: number;
  source: 'ocr' | 'vlm' | 'docling' | 'xml';
  ocr_original?: string;
}

export interface Node2TableStructure {
  id: string;
  page_number: number;
  num_rows: number;
  num_cols: number;
  headers: string[];
  rows_raw: string[][];
}

export interface Node2PageInformation {
  page_number: number;
  width: number;
  height: number;
  elements: Node2LayoutElement[];
  tables: Node2TableStructure[];
}

export interface Node2ProcessingMetadata {
  document_id: string;
  processing_id: string;
  file_type: string;
  mime_type: string;
  file_size_bytes: number;
  page_count: number;
  docling_used: boolean;
  ocr_engine: string;
  ocr_model: string;
  vlm_used: boolean;
  vlm_provider?: string;
  metrics: {
    docling_processing_time: number;
    ocr_processing_time: number;
    vlm_processing_time: number;
    total_processing_time: number;
    vlm_fallback_count: number;
    ocr_low_confidence_count: number;
    total_elements_extracted: number;
  };
}

export interface Node2ParsedDocument {
  document_id: string;
  source: {
    filename: string;
    mime_type: string;
    s3_bucket?: string;
    s3_key?: string;
  };
  pages: Node2PageInformation[];
  tables: Node2TableStructure[];
  elements: Node2LayoutElement[];
  text: string;
  processing: Node2ProcessingMetadata;
}

export interface Node2ProcessResponse {
  document_id: string;
  processing_id: string;
  status: 'completed' | 'failed' | 'processing';
  output_location: string;
  processing_time_seconds: number;
  error_message?: string;
  result?: Node2ParsedDocument;
}

export type PipelineStage =
  | 'start'
  | 'fetch'
  | 'extract'
  | 'comparison'
  | 'compile'
  | 'checker'
  | 'scorecard'
  | 'push'
  | 'finish'
  | 'error';

export interface PipelineEvent {
  stage: PipelineStage;
  loan_id: string;
  status: 'started' | 'running' | 'completed' | 'done' | 'error';
  label?: string;
  subnode_rollups?: Record<string, string>;
  checker_result?: {
    will_retry?: boolean;
    retry_attempt?: number;
    max_retries?: number;
    notes?: string;
  };
  errors?: string[];
  node_history?: string[];
  message?: string;
}

