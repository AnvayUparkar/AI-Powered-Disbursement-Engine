import { apiClient } from './apiClient';

export interface AuthUser {
  username: string;
}

export const authService = {
  /** Current user from the session cookie; rejects with ApiError(401) when logged out. */
  me(): Promise<AuthUser> {
    return apiClient.get<AuthUser>('/auth/me');
  },

  login(username: string, password: string): Promise<AuthUser> {
    return apiClient.post<AuthUser>('/auth/login', { username, password });
  },

  /** Creates a new isolated workspace and signs in. */
  signup(username: string, password: string): Promise<AuthUser> {
    return apiClient.post<AuthUser>('/auth/signup', { username, password });
  },

  logout(): Promise<{ status: string }> {
    return apiClient.post<{ status: string }>('/auth/logout');
  },
};
