import { useState, useEffect } from 'react';
import { Search, Menu, Bell, Clock, Activity } from 'lucide-react';
import { useDebounced } from '@/hooks/useDebounced';
import { useNavigate } from 'react-router-dom';
import { node2Api } from '@/api/node2';

export function Topbar({ onMenu }: { onMenu: () => void }) {
  const [q, setQ] = useState('');
  const [node2Status, setNode2Status] = useState<'connected' | 'offline' | 'checking'>('checking');
  const debounced = useDebounced(q, 350);
  const navigate = useNavigate();

  useEffect(() => {
    node2Api
      .checkHealth()
      .then(() => setNode2Status('connected'))
      .catch(() => setNode2Status('offline'));
  }, []);

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

        <div className="hidden md:flex items-center gap-1.5 text-xs text-ink-500 px-2.5 py-1.5 rounded-md bg-ink-50 border border-ink-200">
          <Clock className="h-3.5 w-3.5" aria-hidden />
          Session 28:14
        </div>
        <button className="btn-ghost px-2 py-1.5 relative" aria-label="Notifications">
          <Bell className="h-5 w-5" />
          <span className="absolute top-1 right-1.5 h-2 w-2 rounded-full bg-discrepancy-500" />
        </button>
      </div>
    </header>
  );
}
