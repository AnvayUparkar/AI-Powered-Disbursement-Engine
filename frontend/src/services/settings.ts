import { apiClient } from './apiClient';

export interface DgclPipelineFlag {
  enabled: boolean;
}

export const settingsService = {
  async getDgclPipelineFlag(): Promise<DgclPipelineFlag> {
    return await apiClient.get<DgclPipelineFlag>('/settings/dgcl-pipeline');
  },

  async setDgclPipelineFlag(enabled: boolean): Promise<DgclPipelineFlag> {
    return await apiClient.post<DgclPipelineFlag>('/settings/dgcl-pipeline', { enabled });
  },
};
