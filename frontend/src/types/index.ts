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
}

export interface Checkpoint {
  id: number;
  name: string;
  status: CheckpointStatus;
  confidence: number;
  reason: string;
  rule: string;
  evidence: Evidence[];
  extractedFields: ExtractedField[];
  validation?: {
    left: string;
    right: string;
    result: 'MATCH' | 'MISMATCH' | 'INCONCLUSIVE';
  };
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

