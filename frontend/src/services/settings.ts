import { apiClient } from './apiClient';

export interface DgclPipelineFlag {
  enabled: boolean;
}

export const settingsService = {
  async getDgclPipelineFlag(): Promise<DgclPipelineFlag> {
    return await apiClient.get<DgclPipelineFlag>('/settings/dgcl-pipeline');
  },

  async setDgclPipelineFlag(enabled: boolean): Promise<DgclPipelineFlag> {
    const res = await apiClient.post<DgclPipelineFlag>('/settings/dgcl-pipeline', { enabled });
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('dgcl-flag-changed', { detail: res.enabled }));
    }
    return res;
  },
};
