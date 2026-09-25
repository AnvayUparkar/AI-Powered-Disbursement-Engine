import { useState, useEffect } from 'react';
import { Search, Menu, Activity, LogOut, ShieldAlert } from 'lucide-react';
import { useDebounced } from '@/hooks/useDebounced';
import { useNavigate } from 'react-router-dom';
import { node2Api } from '@/api/node2';
import { settingsService } from '@/services/settings';
import { useAuth } from '@/context/auth';

export function Topbar({ onMenu }: { onMenu: () => void }) {
  const [q, setQ] = useState('');
  const [node2Status, setNode2Status] = useState<'connected' | 'offline' | 'checking'>('checking');
  const [pipelineEnabled, setPipelineEnabled] = useState(false);
  const [pipelineBusy, setPipelineBusy] = useState(false);
  const debounced = useDebounced(q, 350);
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  useEffect(() => {
    node2Api
      .checkHealth()
      .then(() => setNode2Status('connected'))
      .catch(() => setNode2Status('offline'));
  }, []);

  useEffect(() => {
    settingsService
      .getDgclPipelineFlag()
      .then((flag) => setPipelineEnabled(flag.enabled))
      .catch(() => setPipelineEnabled(false));
  }, []);

  const togglePipeline = async () => {
    const next = !pipelineEnabled;
    if (next && !window.confirm('Turn ON the DGCL verification pipeline? This POC otherwise only runs OCR.')) {
      return;
    }
    setPipelineBusy(true);
    try {
      const flag = await settingsService.setDgclPipelineFlag(next);
      setPipelineEnabled(flag.enabled);
    } catch (e) {
      console.error('Failed to update DGCL pipeline flag:', e);
    } finally {
      setPipelineBusy(false);
    }
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (!debounced.trim()) return;
    if (/^HDB-/i.test(debounced.trim())) {
      navigate(`/cases/${debounced.trim().toUpperCase()}`);
    } else {
      navigate(`/cases?q=${encodeURIComponent(debounced.trim())}`);
    }
  };

  return (
    <header className="h-16 shrink-0 bg-white border-b border-ink-200 flex items-center gap-3 px-4 lg:px-6">
      <button
        onClick={onMenu}
        className="lg:hidden btn-ghost px-2 py-1.5"
        aria-label="Open menu"
      >
        <Menu className="h-5 w-5" />
      </button>

      <form onSubmit={handleSearch} className="relative flex-1 max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-ink-400" aria-hidden />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search case ID or applicant…"
          className="input pl-9"
          aria-label="Search"
        />
      </form>

      <div className="ml-auto flex items-center gap-2">
        <button
          onClick={() => void togglePipeline()}
          disabled={pipelineBusy}
          className={`hidden md:flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-md border transition-colors disabled:opacity-50 ${
            pipelineEnabled
              ? 'bg-verified-50 border-verified-200 text-verified-700 hover:bg-verified-100'
              : 'bg-ink-50 border-ink-200 text-ink-500 hover:bg-ink-100'
          }`}
          title={
            pipelineEnabled
              ? 'DGCL verification pipeline is ON — click to turn off'
              : 'DGCL verification pipeline is OFF (OCR-only) — click to turn on'
          }
        >
          <ShieldAlert className="h-3.5 w-3.5" aria-hidden />
          <span>DGCL Pipeline: {pipelineEnabled ? 'ON' : 'OFF'}</span>
        </button>

        <div
          className={`hidden md:flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-md border ${
            node2Status === 'connected'
              ? 'bg-verified-50 border-verified-200 text-verified-700'
              : node2Status === 'offline'
              ? 'bg-discrepancy-50 border-discrepancy-200 text-discrepancy-700'
              : 'bg-ink-50 border-ink-200 text-ink-500'
          }`}
          title={node2Status === 'connected' ? 'Node 2 FastAPI Engine Connected' : 'Node 2 Service Unreachable'}
        >
          <Activity className="h-3.5 w-3.5" aria-hidden />
          <span>
            Node 2 IDP: {node2Status === 'connected' ? 'Connected' : node2Status === 'offline' ? 'Offline' : 'Connecting...'}
          </span>
        </div>

        {user && (
          <div className="flex items-center gap-1 pl-2 border-l border-ink-200">
            <span className="hidden sm:inline text-sm text-ink-600 max-w-[10rem] truncate" title={user.username}>
              {user.username}
            </span>
            <button onClick={() => void logout()} className="btn-ghost px-2 py-1.5" aria-label="Sign out" title="Sign out">
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
