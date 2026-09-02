import type { SystemSettings } from '@/types';

const delay = (ms = 200) => new Promise((r) => setTimeout(r, ms));

const defaults: SystemSettings = {
  ocrConfidenceThreshold: 85,
  vlmFallbackThreshold: 70,
  humanReviewThreshold: 65,
  processingMode: 'ASSISTED',
  notificationsEnabled: true,
  sessionTimeoutMinutes: 30,
};

export const settingsService = {
  async get(): Promise<SystemSettings> {
    await delay();
    return { ...defaults };
  },
  async save(next: SystemSettings): Promise<SystemSettings> {
    await delay();
    Object.assign(defaults, next);
    return { ...defaults };
  },
};
