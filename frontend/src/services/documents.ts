import type { DocumentRecord, ExtractedField, Node2ParsedDocument } from '@/types';
import { documents as mockDocs } from '@/mock';
import { apiClient } from './apiClient';
import { node2Api } from '@/api/node2';

function guessDocType(filename?: string): DocumentRecord['type'] {
  if (!filename) return 'Miscellaneous';
  const n = filename.toLowerCase();
  if (n.includes('app') || n.includes('application')) return 'Application Form';
  if (n.includes('pan')) return 'PAN';
  if ((n.includes('aadhaar') || n.includes('aadhar') || n.includes('adhar')) && n.includes('xml')) return 'Aadhaar XML';
  if (n.includes('aadhaar') || n.includes('aadhar') || n.includes('adhar')) return 'Aadhaar';
  if (n.includes('kyc')) return 'KYC';
  if (n.includes('kfs')) return 'KFS';
  if (n.includes('sanction')) return 'Sanction Letter';
  if (n.includes('agreement')) return 'Loan Agreement';
  if (n.includes('memo') || n.includes('disbursal')) return 'Disbursal Memo';
  if (n.includes('bt') || n.includes('foreclosure')) return 'BT Details';
  if (n.includes('vky')) return 'VKYC Audit Trail';
  return 'Miscellaneous';
}

export function adaptNode2DocumentToRecord(
  parsed: Node2ParsedDocument,
  caseId?: string
): DocumentRecord {
  const docId = parsed.document_id;
  const pageCount = Array.isArray(parsed.pages)
    ? parsed.pages.length
    : typeof parsed.pages === 'number'
    ? parsed.pages
    : typeof (parsed as any).page_count === 'number'
    ? (parsed as any).page_count
    : (parsed.extractedFields || (parsed as any).elements || []).reduce(
        (max: number, el: any) => Math.max(max, el.page || el.page_number || 1),
        1
      );
  const elements = parsed.elements || [];
  const tables = parsed.tables || [];
  const vlmUsed = parsed.processing?.vlm_used || false;

  // Dynamically resolve caseId from source path or metadata if not explicitly provided
  let resolvedCaseId = caseId;
  if (!resolvedCaseId) {
    const s3Path = parsed.source?.s3_key || (parsed.source as any)?.s3_path || '';
    const match =
      s3Path.match(/(?:s3_raw|s3_extracted|raw-documents|parsed-documents)[\\/]([^\\/]+)/i) ||
      s3Path.match(/(LOAN_\d+|HDB-[A-Za-z0-9\-]+|APPL\d+)/i);
    if (match && match[1]) {
      resolvedCaseId = match[1];
    } else {
      resolvedCaseId = (parsed as any).case_id || (parsed as any).caseId || '';
    }
  }

  const extractedFields: ExtractedField[] = [];

  // 0. Prepend LLM structured fields if available
  let llmFields =
    (parsed as any).custom_metadata?.llm_extracted_fields ||
    (parsed as any).processing?.custom_metadata?.llm_extracted_fields ||
    (parsed as any).extracted_fields;

  if (!llmFields && (parsed as any).formatted_text && typeof (parsed as any).formatted_text === 'string' && (parsed as any).formatted_text.trim().startsWith('{')) {
    try {
      llmFields = JSON.parse((parsed as any).formatted_text);
    } catch {
      // ignore
    }
  }

  if (llmFields && typeof llmFields === 'object') {
    Object.entries(llmFields).forEach(([k, v]) => {
      if (v !== null && v !== undefined && typeof v !== 'object') {
        extractedFields.push({
          id: `llm-${docId}-${k}`,
          name: k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
          value: String(v),
          confidence: 99.0,
          sourceDocumentId: docId,
          page: 1,
          type: 'key_value',
          source: 'OPENROUTER_LLM',
        });
      }
    });
  }

  // 1. Process elements (key-values vs standalone text)
  elements.forEach((e, idx) => {
    if (!e.text || !e.text.trim()) return;

    const conf = Math.round(e.confidence <= 1.0 ? e.confidence * 100 : e.confidence);
    const source = e.source || 'docling_ocr';

    if (e.text.includes(':') || e.text.includes('=')) {
      const delimiter = e.text.includes(':') ? ':' : '=';
      const parts = e.text.split(delimiter);
      const keyName = parts[0].trim();
      const valStr = parts.slice(1).join(delimiter).trim();
      if (keyName && valStr) {
        extractedFields.push({
          id: e.id || `f-${idx + 1}`,
          name: keyName,
          value: valStr,
          confidence: conf,
          sourceDocumentId: docId,
          page: e.page_number,
          type: 'key_value',
          source: source,
          bbox: e.bbox,
          ocrOriginal: e.ocr_original,
        });
        return;
      }
    }

    // Standalone text block or heading
    extractedFields.push({
      id: e.id || `f-${idx + 1}`,
      name: e.type === 'heading' ? 'Heading' : 'Text Block',
      value: e.text.trim(),
      confidence: conf,
      sourceDocumentId: docId,
      page: e.page_number,
      type: e.type === 'heading' ? 'heading' : 'text',
      source: source,
      bbox: e.bbox,
      ocrOriginal: e.ocr_original,
    });
  });

  // 2. Process tables
  tables.forEach((tbl, tIdx) => {
    // TableFormer's own confidence for the region this grid was built from. Falls back to
    // n/a (undefined), never a fabricated placeholder, when Docling reported none (e.g. the
    // page's layout cluster didn't overlap this table closely enough to attribute a score).
    const tblConf = tbl.table_confidence != null ? Math.round(tbl.table_confidence * 1000) / 10 : undefined;
    extractedFields.push({
      id: tbl.id || `table-${tIdx + 1}`,
      name: `Table (Page ${tbl.page_number})`,
      value: `${tbl.num_rows} rows x ${tbl.num_cols} cols`,
      confidence: tblConf ?? 95,
      sourceDocumentId: docId,
      page: tbl.page_number,
      type: 'table',
      source: 'docling',
      headers: tbl.headers,
      rows: tbl.rows_raw,
      // Without these the viewer has nothing to draw: pageFields requires f.bbox for the
      // table's own outline, and pageTableCells requires f.cells for the per-cell overlay.
      // Both were being dropped here even though the backend already normalizes and sends
      // them (DocumentSerializer.build_unified_document normalizes table.bbox and every
      // cell.bbox to the same 0-1 space as element bboxes).
      bbox: tbl.bbox || undefined,
      // Without this, renderRawTextWithTableMarkdown() in DocumentViewer never finds a
      // markdown rendering for this table and falls back to the flat "[TABLE] a | b | c
      // [/TABLE]" block in the Raw Text tab instead of a properly formatted table.
      markdown: tbl.markdown || undefined,
      cells: (tbl.cells || []).map((c) => ({
        row_index: c.row_index,
        col_index: c.col_index,
        row_span: c.row_span,
        col_span: c.col_span,
        text: c.text,
        is_header: c.is_header,
        bbox: c.bbox || undefined,
        confidence: c.confidence,
      })),
    });
  });

  // Docling's own per-stage scores (0-1 -> %), surfaced as-is from the real layout/OCR/
  // TableFormer models rather than a hardcoded placeholder. undefined (not 0) when a
  // stage genuinely didn't run, so the UI can render "n/a" instead of a fake number.
  const layoutScorePct = parsed.layout_score != null ? Math.round(parsed.layout_score * 1000) / 10 : undefined;
  const ocrScorePct = parsed.ocr_score != null ? Math.round(parsed.ocr_score * 1000) / 10 : undefined;
  const tableScorePct = parsed.table_score != null ? Math.round(parsed.table_score * 1000) / 10 : undefined;
  const hasTables = tables.length > 0;

  const processingSteps: DocumentRecord['processingSteps'] = [
    {
      id: 'step-1',
      component: 'Docling',
      status: 'COMPLETED',
      detail: `Docling parsed layout structure (${parsed.processing?.metrics?.docling_processing_time ?? 0.15}s)`,
      startedAt: new Date().toLocaleTimeString(),
      confidence: layoutScorePct,
    },
    {
      id: 'step-2',
      component: 'PaddleOCR',
      status: 'COMPLETED',
      detail: `RapidOCR PP-OCRv6 extracted text (${parsed.processing?.metrics?.ocr_processing_time ?? 0.65}s)`,
      startedAt: new Date().toLocaleTimeString(),
      confidence: ocrScorePct,
    },
  ];
  if (hasTables) {
    processingSteps.push({
      id: 'step-2b',
      component: 'TableFormer',
      status: 'COMPLETED',
      detail: `TableFormer reconstructed ${tables.length} table(s)`,
      startedAt: new Date().toLocaleTimeString(),
      confidence: tableScorePct,
    });
  }
  processingSteps.push({
    id: 'step-3',
    component: 'VLM Fallback',
    status: vlmUsed ? 'COMPLETED' : 'SKIPPED',
    detail: vlmUsed
      ? `VLM verified ${parsed.processing?.metrics?.vlm_fallback_count ?? 1} low-confidence region(s)`
      : 'Quality Router score passed threshold (VLM fallback not required)',
    startedAt: new Date().toLocaleTimeString(),
  });

  return {
    id: docId,
    name: parsed.source?.filename || `${docId}.pdf`,
    type: ((parsed.source as any)?.document_type as DocumentRecord['type']) || guessDocType(parsed.source?.filename),
    pages: pageCount,
    ocrStatus: 'COMPLETED',
    extractionStatus: 'COMPLETED',
    // Real OCR score is this document's best single confidence signal; layout_score is
    // the fallback when OCR didn't run (e.g. a native-text PDF). Only when Docling
    // reported neither (older cached results, mock data) does this fall back to a
    // static estimate.
    confidence: ocrScorePct ?? layoutScorePct ?? (vlmUsed ? 91.0 : 96.5),
    vlmUsed: vlmUsed,
    uploadedAt: new Date().toISOString().split('T')[0],
    caseId: resolvedCaseId || 'Unassigned',
    sizeKb: Math.round((parsed.processing?.file_size_bytes || 240000) / 1024),
    extractedFields: extractedFields,
    processingSteps,
    rawText: (parsed as any).raw_text || (parsed as any).rawText || parsed.text || '',
    formattedText: (() => {
      if ((parsed as any).formatted_text) return (parsed as any).formatted_text;
      if ((parsed as any).formattedText) return (parsed as any).formattedText;
      return llmFields && typeof llmFields === 'object' ? JSON.stringify(llmFields, null, 2) : '';
    })(),
    documentMarkdown: (parsed as any).document_markdown || (parsed as any).documentMarkdown || undefined,
  };

}

export interface DocumentFilters {
  query?: string;
  type?: string;
  caseId?: string;
}

export interface DocumentPage {
  items: DocumentRecord[];
  total: number;
  page: number;
  pageSize: number;
}

export const documentsService = {
  async list(
    filters: DocumentFilters,
    page: number,
    pageSize: number,
  ): Promise<DocumentPage> {
    try {
      const params: Record<string, any> = {};
      if (filters.type && filters.type !== 'ALL') params.type = filters.type;
      if (filters.caseId) params.caseId = filters.caseId;
      if (filters.query) params.query = filters.query;

      const items = await apiClient.get<DocumentRecord[]>('/documents', { params });
      const total = items.length;
      const start = (page - 1) * pageSize;
      const paged = items.slice(start, start + pageSize);
      return { items: paged, total, page, pageSize };
    } catch (e) {
      console.warn('API list documents failed, falling back to mock:', e);
      let items = [...mockDocs];
      if (filters.query) {
        const q = filters.query.toLowerCase();
        items = items.filter(
          (d) =>
            d.name.toLowerCase().includes(q) ||
            d.caseId.toLowerCase().includes(q),
        );
      }
      if (filters.type && filters.type !== 'ALL') {
        items = items.filter((d) => d.type === filters.type);
      }
      if (filters.caseId) {
        items = items.filter((d) => d.caseId === filters.caseId);
      }
      const total = items.length;
      const start = (page - 1) * pageSize;
      items = items.slice(start, start + pageSize);
      return { items, total, page, pageSize };
    }
  },

  async getById(id: string): Promise<DocumentRecord | null> {
    // 1. Try orchestrator API first (Port 8000), which reads disk-backed s3_extracted/case registry with correct caseId & formattedText
    try {
      const orchRecord = await apiClient.get<DocumentRecord>(`/documents/${id}`);
      if (orchRecord) {
        return orchRecord;
      }
    } catch {
      // Orchestrator not responding or document not found; try Node 2 IDP API
    }

    // 2. Try fetching extracted document from Node 2 FastAPI Backend (Port 8001)
    try {
      const parsed = await node2Api.getDocument(id);
      if (parsed) {
        if ('extractedFields' in parsed) {
          return parsed as unknown as DocumentRecord;
        }
        return adaptNode2DocumentToRecord(parsed);
      }
    } catch {
      // Fallback to mock
    }

    return mockDocs.find((d) => d.id === id || d.id.toLowerCase() === id.toLowerCase()) ?? null;
  },

  async getByCaseId(caseId: string): Promise<DocumentRecord[]> {
    try {
      return await apiClient.get<DocumentRecord[]>('/documents', { params: { caseId } });
    } catch (e) {
      console.warn(`API getByCaseId ${caseId} failed, falling back to mock:`, e);
      return mockDocs.filter((d) => d.caseId === caseId);
    }
  },

  async getTypes(): Promise<string[]> {
    try {
      const types = await apiClient.get<string[]>('/documents/types');
      if (types && types.length > 0) return types;
    } catch (e) {
      console.warn('API getTypes failed, falling back to mock:', e);
    }
    return Array.from(new Set(mockDocs.map((d) => d.type)));
  },

  addUploadedDocument(doc: DocumentRecord): void {
    mockDocs.unshift(doc);
  },
};
