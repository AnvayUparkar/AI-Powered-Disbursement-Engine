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
  caseId: string = 'HDB-2026-001245'
): DocumentRecord {
  const docId = parsed.document_id;
  const pageCount = parsed.pages?.length || 1;
  const elements = parsed.elements || [];
  const tables = parsed.tables || [];
  const vlmUsed = parsed.processing?.vlm_used || false;

  const extractedFields: ExtractedField[] = [];

  // 1. Process elements (key-values vs standalone text)
  elements.forEach((e, idx) => {
    if (!e.text || !e.text.trim()) return;

    const conf = Math.round(e.confidence <= 1.0 ? e.confidence * 100 : e.confidence);
    const source = e.source || 'ocr';

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
    extractedFields.push({
      id: tbl.id || `table-${tIdx + 1}`,
      name: `Table (Page ${tbl.page_number})`,
      value: `${tbl.num_rows} rows x ${tbl.num_cols} cols`,
      confidence: 95,
      sourceDocumentId: docId,
      page: tbl.page_number,
      type: 'table',
      source: 'docling',
      headers: tbl.headers,
      rows: tbl.rows_raw,
    });
  });

  return {
    id: docId,
    name: parsed.source?.filename || `${docId}.pdf`,
    type: ((parsed.source as any)?.document_type as DocumentRecord['type']) || guessDocType(parsed.source?.filename),
    pages: pageCount,
    ocrStatus: 'COMPLETED',
    extractionStatus: 'COMPLETED',
    confidence: vlmUsed ? 91.0 : 96.5,
    vlmUsed: vlmUsed,
    uploadedAt: new Date().toISOString().split('T')[0],
    caseId: caseId,
    sizeKb: Math.round((parsed.processing?.file_size_bytes || 240000) / 1024),
    extractedFields: extractedFields,
    processingSteps: [
      {
        id: 'step-1',
        component: 'Docling',
        status: 'COMPLETED',
        detail: `Docling parsed layout structure (${parsed.processing?.metrics?.docling_processing_time ?? 0.15}s)`,
        startedAt: new Date().toLocaleTimeString(),
      },
      {
        id: 'step-2',
        component: 'PaddleOCR',
        status: 'COMPLETED',
        detail: `RapidOCR PP-OCRv6 extracted text (${parsed.processing?.metrics?.ocr_processing_time ?? 0.65}s)`,
        startedAt: new Date().toLocaleTimeString(),
        confidence: 95.0,
      },
      {
        id: 'step-3',
        component: 'VLM Fallback',
        status: vlmUsed ? 'COMPLETED' : 'SKIPPED',
        detail: vlmUsed
          ? `VLM verified ${parsed.processing?.metrics?.vlm_fallback_count ?? 1} low-confidence region(s)`
          : 'Quality Router score passed threshold (VLM fallback not required)',
        startedAt: new Date().toLocaleTimeString(),
      },
    ],
    rawText: parsed.text || '',
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
    // 1. Try fetching real extracted document from Node 2 FastAPI Backend
    try {
      const parsed = await node2Api.getDocument(id);
      if (parsed) {
        if ('extractedFields' in parsed) {
          return parsed as unknown as DocumentRecord;
        }
        return adaptNode2DocumentToRecord(parsed);
      }
    } catch {
      // Backend not running or document not found in backend store; fallback to orchestrator API or mock
    }

    // 2. Try orchestrator API
    try {
      return await apiClient.get<DocumentRecord>(`/documents/${id}`);
    } catch (e) {
      console.warn(`API get document ${id} failed, falling back to mock:`, e);
      return mockDocs.find((d) => d.id === id || d.id.toLowerCase() === id.toLowerCase()) ?? null;
    }
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
