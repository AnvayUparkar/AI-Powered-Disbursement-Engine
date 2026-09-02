import type { Case, Checkpoint } from '@/types';
import { casesService } from './cases';
import { cases as mockCases } from '@/mock';

export const verificationService = {
  async getCaseWithScorecard(id: string): Promise<Case | null> {
    const c = await casesService.getById(id);
    return c ?? mockCases.find((item) => item.id === id) ?? null;
  },

  async getCheckpoint(caseId: string, checkpointId: number): Promise<Checkpoint | null> {
    const c = await this.getCaseWithScorecard(caseId);
    return c?.checkpoints.find((cp) => cp.id === checkpointId) ?? null;
  },

  async getCheckpoints(caseId: string): Promise<Checkpoint[]> {
    const c = await this.getCaseWithScorecard(caseId);
    return c?.checkpoints ?? [];
  },
};
