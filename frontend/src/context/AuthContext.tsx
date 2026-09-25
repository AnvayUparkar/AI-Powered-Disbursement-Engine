import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { UNAUTHORIZED_EVENT } from '@/services/apiClient';
import { authService } from '@/services/auth';
import { AuthContext } from './auth';
import type { AuthStatus } from './auth';
import type { AuthUser } from '@/services/auth';

/**
 * Every auth transition does a full page load so nothing cached in memory by one
 * workspace (module state, React state) can ever be shown to another.
 */
function reloadAt(path: string) {
  window.location.assign(path);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    let cancelled = false;
    authService
      .me()
      .then((u) => {
        if (cancelled) return;
        setUser(u);
        setStatus('authenticated');
      })
      .catch(() => {
        if (cancelled) return;
        setUser(null);
        setStatus('anonymous');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // A data request came back 401 (session expired): drop to the login screen.
  useEffect(() => {
    const onUnauthorized = () => {
      setUser(null);
      setStatus('anonymous');
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    await authService.login(username, password);
    reloadAt('/');
  }, []);

  const signup = useCallback(async (username: string, password: string) => {
    await authService.signup(username, password);
    reloadAt('/');
  }, []);

  const logout = useCallback(async () => {
    try {
      await authService.logout();
    } finally {
      reloadAt('/');
    }
  }, []);

  const value = useMemo(
    () => ({ status, user, login, signup, logout }),
    [status, user, login, signup, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
