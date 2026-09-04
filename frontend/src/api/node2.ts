import type {
  Node2HealthResponse,
  Node2ProcessRequest,
  Node2ProcessResponse,
  Node2ParsedDocument,
} from '@/types';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001').replace(/\/$/, '');

export class ApiError extends Error {
  status: number;
  code?: string;
  details?: string;

  constructor(message: string, status: number, code?: string, details?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let errMessage = `HTTP Error ${res.status}: ${res.statusText}`;
    let code: string | undefined;
    let details: string | undefined;

    try {
      const data = await res.json();
      if (data.detail) {
        if (typeof data.detail === 'object') {
          errMessage = data.detail.message || errMessage;
          code = data.detail.error;
          details = data.detail.details;
        } else if (typeof data.detail === 'string') {
          errMessage = data.detail;
        }
      } else if (data.message) {
        errMessage = data.message;
      }
    } catch {
      // Fall back to status text
    }

    throw new ApiError(errMessage, res.status, code, details);
  }

  return res.json() as Promise<T>;
}

export const node2Api = {
  /**
   * Health check endpoint to verify Node 2 connection status.
   */
  async checkHealth(): Promise<Node2HealthResponse> {
    try {
      const res = await fetch(`${API_BASE_URL}/health`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      });
      return await handleResponse<Node2HealthResponse>(res);
    } catch (err) {
      if (err instanceof ApiError) throw err;
      throw new ApiError('Node 2 IDP service is currently unreachable', 503, 'NetworkError');
    }
  },

  /**
   * Process a document registered in S3 by document_id and s3_key.
   */
  async processDocument(req: Node2ProcessRequest): Promise<Node2ProcessResponse> {
    const res = await fetch(`${API_BASE_URL}/api/v1/documents/process`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify(req),
    });
    return handleResponse<Node2ProcessResponse>(res);
  },

  /**
   * Direct file upload from browser to Node 2 pipeline via multipart/form-data.
   */
  async uploadAndProcess(
    file: File,
    documentId?: string,
    s3Bucket?: string,
    caseId?: string,
    docType?: string
  ): Promise<Node2ProcessResponse> {
    const formData = new FormData();
    formData.append('file', file);
    if (documentId) formData.append('document_id', documentId);
    if (s3Bucket) formData.append('s3_bucket', s3Bucket);
    if (caseId) formData.append('case_id', caseId);
    if (docType) formData.append('doc_type', docType);

    const res = await fetch(`${API_BASE_URL}/api/v1/documents/upload`, {
      method: 'POST',
      body: formData,
    });
    return handleResponse<Node2ProcessResponse>(res);
  },

  /**
   * Retrieve ParsedDocument JSON output for a given document_id.
   */
  async getDocument(documentId: string): Promise<Node2ParsedDocument> {
    const res = await fetch(`${API_BASE_URL}/api/v1/documents/${encodeURIComponent(documentId)}`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    });
    return handleResponse<Node2ParsedDocument>(res);
  },
};
