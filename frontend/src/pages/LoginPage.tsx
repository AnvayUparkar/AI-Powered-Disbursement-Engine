import { useState } from 'react';
import { ApiError } from '@/services/apiClient';
import { useAuth } from '@/context/auth';

type Mode = 'login' | 'signup';

const USERNAME_PATTERN = /^[A-Za-z0-9_.-]{3,32}$/;
const MIN_PASSWORD_LENGTH = 8;
const MAX_PASSWORD_LENGTH = 128;

function validate(mode: Mode, username: string, password: string, confirm: string): string | null {
  if (mode === 'login') {
    return username && password ? null : 'Enter your username and password.';
  }
  if (!USERNAME_PATTERN.test(username)) {
    return "Username must be 3-32 characters: letters, digits, '.', '_' or '-'.";
  }
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (password.length > MAX_PASSWORD_LENGTH) {
    return `Password must be at most ${MAX_PASSWORD_LENGTH} characters.`;
  }
  if (password !== confirm) return 'Passwords do not match.';
  return null;
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = (err.data as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string') return detail;
    if (err.status === 422) return 'Please check the username and password you entered.';
  }
  return 'Could not reach the server. Please try again.';
}

export default function LoginPage() {
  const { login, signup } = useAuth();
  const [mode, setMode] = useState<Mode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const switchMode = (next: Mode) => {
    setMode(next);
    setError(null);
    setConfirm('');
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const problem = validate(mode, username.trim(), password, confirm);
    if (problem) {
      setError(problem);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await (mode === 'login' ? login : signup)(username.trim(), password);
    } catch (err) {
      setError(errorMessage(err));
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-50 px-4">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-6">
          <img src="/hdb.png" alt="HDB Disbursal Intelligence" className="h-14 w-14 object-contain" />
          <h1 className="mt-3 text-lg font-semibold text-ink-800">Disbursal Intelligence</h1>
          <p className="text-sm text-ink-500">
            {mode === 'login' ? 'Sign in to your workspace' : 'Create your own private workspace'}
          </p>
        </div>

        <div className="card p-6">
          <div className="grid grid-cols-2 gap-1 mb-5 rounded-md bg-ink-100 p-1" role="tablist">
            {(['login', 'signup'] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={mode === m}
                onClick={() => switchMode(m)}
                className={`rounded px-3 py-1.5 text-sm font-medium transition-colors ${
                  mode === m ? 'bg-white text-ink-800 shadow-sm' : 'text-ink-500 hover:text-ink-700'
                }`}
              >
                {m === 'login' ? 'Sign in' : 'Create account'}
              </button>
            ))}
          </div>

          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            <div>
              <label htmlFor="username" className="block text-sm font-medium text-ink-700 mb-1">
                Username
              </label>
              <input
                id="username"
                className="input"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="password" className="block text-sm font-medium text-ink-700 mb-1">
                Password
              </label>
              <input
                id="password"
                type="password"
                className="input"
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {mode === 'signup' && (
              <div>
                <label htmlFor="confirm" className="block text-sm font-medium text-ink-700 mb-1">
                  Confirm password
                </label>
                <input
                  id="confirm"
                  type="password"
                  className="input"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </div>
            )}

            {error && (
              <p role="alert" className="text-sm text-discrepancy-700 bg-discrepancy-50 border border-discrepancy-200 rounded-md px-3 py-2">
                {error}
              </p>
            )}

            <button type="submit" className="btn-primary w-full" disabled={submitting}>
              {submitting ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
            </button>
          </form>

          {mode === 'signup' && (
            <p className="mt-4 text-xs text-ink-500">
              Each account gets its own private workspace. Data is never shared between accounts, and
              there is no password recovery, so keep your password safe.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
