import { createContext, useContext } from 'react';
import type { AuthUser } from '@/services/auth';

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous';

export interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  login: (username: string, password: string) => Promise<void>;
  signup: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
