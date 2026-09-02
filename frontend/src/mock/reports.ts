import type {
  CheckpointPerformance,
  DashboardKpis,
  ReportSummary,
} from '@/types';
import { DGCL_CHECKPOINT_NAMES } from './helpers';

export const dashboardKpis: DashboardKpis = {
  casesProcessedToday: 200,
  documentsProcessed: 1842,
  verified: 1527,
  discrepancies: 184,
  needsReview: 131,
  dgclValidation: 94.2,
  dgclTarget: 90,
  avgProcessingSeconds: 222,
  avgProcessingTargetSeconds: 300,
  docProcessedToday: 1842,
  ocrSuccessRate: 96.4,
  vlmFallbackRate: 7.8,
  extractionSuccessRate: 95.1,
  avgDocProcessingSeconds: 41,
};

export const checkpointPerformance: CheckpointPerformance[] =
  DGCL_CHECKPOINT_NAMES.map((name, i) => ({
    id: i + 1,
    name,
    passRate: [
      98.4, 99.1, 94.8, 96.3, 98.9, 97.2, 95.7, 96.1, 99.4, 93.8, 91.7, 92.4,
    ][i],
  }));

const days = ['Aug 25', 'Aug 26', 'Aug 27', 'Aug 28', 'Aug 29', 'Aug 30', 'Aug 31'];

export const reportSummary: ReportSummary = {
  totalCases: 1240,
  verified: 1043,
  discrepancies: 121,
  indeterminate: 76,
  avgProcessingSeconds: 231,
  vlmFallbackPct: 7.8,
  checkpointPerformance,
  discrepancyTrend: [
    { day: 'Aug 25', count: 14 },
    { day: 'Aug 26', count: 18 },
    { day: 'Aug 27', count: 11 },
    { day: 'Aug 28', count: 22 },
    { day: 'Aug 29', count: 19 },
    { day: 'Aug 30', count: 27 },
    { day: 'Aug 31', count: 24 },
  ],
  reviewWorkload: [
    { day: 'Aug 25', created: 16, resolved: 14 },
    { day: 'Aug 26', created: 20, resolved: 18 },
    { day: 'Aug 27', created: 13, resolved: 15 },
    { day: 'Aug 28', created: 24, resolved: 20 },
    { day: 'Aug 29', created: 21, resolved: 19 },
    { day: 'Aug 30', created: 29, resolved: 24 },
    { day: 'Aug 31', created: 26, resolved: 21 },
  ],
  processingLatency: [
    { day: 'Aug 25', seconds: 218 },
    { day: 'Aug 26', seconds: 235 },
    { day: 'Aug 27', seconds: 209 },
    { day: 'Aug 28', seconds: 248 },
    { day: 'Aug 29', seconds: 224 },
    { day: 'Aug 30', seconds: 261 },
    { day: 'Aug 31', seconds: 231 },
  ],
  extractionAccuracy: days.map((day, i) => ({
    day,
    pct: [94.2, 95.1, 93.8, 96.0, 95.4, 94.9, 95.1][i],
  })),
  vlmFallbackTrend: days.map((day, i) => ({
    day,
    pct: [6.4, 7.1, 8.2, 6.9, 7.8, 8.6, 7.8][i],
  })),
};
