import type { ReviewItem } from '@/types';
import { reviewItems as mockReviews } from '@/mock';
import { apiClient } from './apiClient';

export interface ReviewFilters {
  query?: string;
  priority?: string;
  assigned?: 'ALL' | 'UNASSIGNED' | 'ASSIGNED';
  caseId?: string;
}

export interface ReviewPage {
  items: ReviewItem[];
  total: number;
  page: number;
  pageSize: number;
}

export const reviewService = {
  async list(
    filters: ReviewFilters,
    page: number,
    pageSize: number,
  ): Promise<ReviewPage> {
    try {
      const params: Record<string, any> = {};
      if (filters.priority && filters.priority !== 'ALL') params.priority = filters.priority;
      if (filters.caseId) params.caseId = filters.caseId;

      const items = await apiClient.get<ReviewItem[]>('/reviews', { params });
      let filtered = [...items];

      if (filters.query) {
        const q = filters.query.toLowerCase();
        filtered = filtered.filter(
          (r) =>
            r.caseId.toLowerCase().includes(q) ||
            r.issue.toLowerCase().includes(q) ||
            r.checkpointName.toLowerCase().includes(q),
        );
      }
      if (filters.assigned === 'UNASSIGNED') {
        filtered = filtered.filter((r) => !r.assignedTo);
      } else if (filters.assigned === 'ASSIGNED') {
        filtered = filtered.filter((r) => !!r.assignedTo);
      }

      const total = filtered.length;
      const start = (page - 1) * pageSize;
      const paged = filtered.slice(start, start + pageSize);
      return { items: paged, total, page, pageSize };
    } catch (e) {
      console.warn('API list reviews failed, falling back to mock:', e);
      let items = [...mockReviews];
      if (filters.query) {
        const q = filters.query.toLowerCase();
        items = items.filter(
          (r) =>
            r.caseId.toLowerCase().includes(q) ||
            r.issue.toLowerCase().includes(q) ||
            r.checkpointName.toLowerCase().includes(q),
        );
      }
      if (filters.priority && filters.priority !== 'ALL') {
        items = items.filter((r) => r.priority === filters.priority);
      }
      if (filters.assigned === 'UNASSIGNED') {
        items = items.filter((r) => !r.assignedTo);
      } else if (filters.assigned === 'ASSIGNED') {
        items = items.filter((r) => !!r.assignedTo);
      }
      const total = items.length;
      const start = (page - 1) * pageSize;
      items = items.slice(start, start + pageSize);
      return { items, total, page, pageSize };
    }
  },

  async getById(id: string): Promise<ReviewItem | null> {
    try {
      return await apiClient.get<ReviewItem>(`/reviews/${id}`);
    } catch (e) {
      console.warn(`API get review ${id} failed, falling back to mock:`, e);
      return mockReviews.find((r) => r.id === id) ?? null;
    }
  },

  async getByCaseId(caseId: string): Promise<ReviewItem[]> {
    try {
      return await apiClient.get<ReviewItem[]>('/reviews', { params: { caseId } });
    } catch (e) {
      console.warn(`API getByCaseId ${caseId} failed, falling back to mock:`, e);
      return mockReviews.filter((r) => r.caseId === caseId);
    }
  },

  async assign(id: string, assignee: string): Promise<ReviewItem | null> {
    const item = await this.getById(id);
    if (item) item.assignedTo = assignee;
    return item;
  },

  async resolve(
    id: string,
    decision: 'CONFIRM' | 'CORRECT' | 'REJECT',
    correctedValue?: string,
  ): Promise<ReviewItem | null> {
    try {
      const decisionMap: Record<string, string> = {
        CONFIRM: 'APPROVE',
        CORRECT: 'OVERRIDE',
        REJECT: 'REJECT',
      };
      await apiClient.post(`/reviews/${id}/adjudicate`, {
        decision: decisionMap[decision] || decision,
        notes: correctedValue ? `Corrected value: ${correctedValue}` : `Adjudicated as ${decision}`,
        assignedTo: 'Credit Officer',
      });
      return await this.getById(id);
    } catch (e) {
      console.warn(`API adjudicate review ${id} failed, falling back:`, e);
      const item = mockReviews.find((r) => r.id === id);
      return item ?? null;
    }
  },
};
