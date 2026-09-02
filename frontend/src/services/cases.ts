import type { Case, CaseStatus, RiskLevel } from '@/types';
import { cases as mockCases } from '@/mock';
import { apiClient } from './apiClient';

export interface CaseFilters {
  query?: string;
  status?: CaseStatus | 'ALL';
  risk?: RiskLevel | 'ALL';
  loanType?: string;
  dateFrom?: string;
  dateTo?: string;
}

export interface CasePage {
  items: Case[];
  total: number;
  page: number;
  pageSize: number;
}

export interface SortState {
  key: keyof Case;
  dir: 'asc' | 'desc';
}

export const casesService = {
  async list(
    filters: CaseFilters,
    sort: SortState | null,
    page: number,
    pageSize: number,
  ): Promise<CasePage> {
    try {
      const params: Record<string, any> = {
        page,
        pageSize,
      };
      if (filters.query) params.query = filters.query;
      if (filters.status && filters.status !== 'ALL') params.status = filters.status;
      if (filters.risk && filters.risk !== 'ALL') params.risk = filters.risk;
      if (filters.loanType && filters.loanType !== 'ALL') params.loanType = filters.loanType;
      if (filters.dateFrom) params.dateFrom = filters.dateFrom;
      if (filters.dateTo) params.dateTo = filters.dateTo;
      if (sort) {
        params.sortKey = sort.key;
        params.sortDir = sort.dir;
      }

      return await apiClient.get<CasePage>('/cases', { params });
    } catch (e) {
      console.warn('API list cases failed, falling back to mock data:', e);
      // Fallback to mock data
      let items = [...mockCases];
      if (filters.query) {
        const q = filters.query.toLowerCase();
        items = items.filter(
          (c) =>
            c.id.toLowerCase().includes(q) ||
            c.applicant.toLowerCase().includes(q) ||
            c.applicationId.toLowerCase().includes(q),
        );
      }
      if (filters.status && filters.status !== 'ALL') {
        items = items.filter((c) => c.status === filters.status);
      }
      if (filters.risk && filters.risk !== 'ALL') {
        items = items.filter((c) => c.riskLevel === filters.risk);
      }
      if (filters.loanType && filters.loanType !== 'ALL') {
        items = items.filter((c) => c.loanType === filters.loanType);
      }
      const total = items.length;
      const start = (page - 1) * pageSize;
      items = items.slice(start, start + pageSize);
      return { items, total, page, pageSize };
    }
  },

  async getById(id: string): Promise<Case | null> {
    try {
      return await apiClient.get<Case>(`/cases/${id}`);
    } catch (e) {
      console.warn(`API get case ${id} failed, falling back to mock:`, e);
      return mockCases.find((c) => c.id === id) ?? null;
    }
  },

  async getRecent(limit = 8): Promise<Case[]> {
    try {
      return await apiClient.get<Case[]>('/cases/recent', { params: { limit } });
    } catch (e) {
      console.warn('API getRecent failed, falling back to mock:', e);
      return [...mockCases].slice(0, limit);
    }
  },

  async getLoanTypes(): Promise<string[]> {
    try {
      return await apiClient.get<string[]>('/cases/loan-types');
    } catch (e) {
      console.warn('API getLoanTypes failed, falling back to mock:', e);
      return Array.from(new Set(mockCases.map((c) => c.loanType)));
    }
  },

  async getNextCaseId(): Promise<string> {
    try {
      const res = await apiClient.get<{ nextId: string }>('/cases/next-id');
      return res.nextId;
    } catch (e) {
      console.warn('API getNextCaseId failed, generating fallback:', e);
      return `LOAN_${(mockCases.length + 1).toString().padStart(3, '0')}`;
    }
  },

  async createCase(payload: {
    case_id?: string;
    applicant_name?: string;
    loan_type?: string;
    loan_amount?: number;
    tenure_months?: number;
  }): Promise<{ caseId: string; status: string; case?: any }> {
    return await apiClient.post<{ caseId: string; status: string; case?: any }>('/cases/create', payload);
  },

  streamPipeline(
    caseId: string,
    onEvent: (evt: import('@/types').PipelineEvent) => void,
    onError?: (err: any) => void,
  ): () => void {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api';
    const url = `${baseUrl}/cases/${caseId}/stream`;
    const eventSource = new EventSource(url);

    eventSource.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as import('@/types').PipelineEvent;
        onEvent(data);
        if (data.stage === 'finish' || data.stage === 'error') {
          eventSource.close();
        }
      } catch (err) {
        console.error('Error parsing SSE event:', err);
      }
    };

    eventSource.onerror = (err) => {
      console.warn('SSE pipeline stream error/closed:', err);
      eventSource.close();
      onError?.(err);
    };

    return () => {
      eventSource.close();
    };
  },

  async runVerification(id: string): Promise<{ status: string; case: Case }> {
    return await apiClient.post<{ status: string; case: Case }>(`/cases/${id}/run`);
  },

  async getStatus(id: string): Promise<any> {
    return await apiClient.get<any>(`/cases/${id}/status`);
  },
};

