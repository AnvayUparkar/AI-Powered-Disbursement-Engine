import type { AuditEvent, DashboardKpis, ReportSummary } from '@/types';
import { auditEvents, dashboardKpis, reportSummary } from '@/mock';
import { apiClient } from './apiClient';

export const reportsService = {
  async getDashboardKpis(): Promise<DashboardKpis> {
    try {
      return await apiClient.get<DashboardKpis>('/dashboard/kpis');
    } catch (e) {
      console.warn('API getDashboardKpis failed, falling back to mock:', e);
      return dashboardKpis;
    }
  },
  async getReportSummary(): Promise<ReportSummary> {
    try {
      return await apiClient.get<ReportSummary>('/reports/summary');
    } catch (e) {
      console.warn('API getReportSummary failed, falling back to mock:', e);
      return reportSummary;
    }
  },
};

export const auditService = {
  async list(caseId?: string): Promise<AuditEvent[]> {
    if (caseId) return auditEvents.filter((e) => e.caseId === caseId);
    return auditEvents;
  },
};
