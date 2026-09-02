import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  FolderKanban,
  FileText,
  ShieldCheck,
  ClipboardList,
  BarChart3,
  History,
  Settings,
  Activity,
  ShieldAlert,
} from 'lucide-react';

const nav = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/cases', label: 'Loan Cases', icon: FolderKanban },
  { to: '/documents', label: 'Documents', icon: FileText },
  { to: '/verification', label: 'Verification', icon: ShieldCheck },
  { to: '/review', label: 'Review Queue', icon: ClipboardList },
  { to: '/reports', label: 'Reports', icon: BarChart3 },
  { to: '/audit', label: 'Audit Log', icon: History },
  { to: '/settings', label: 'Settings', icon: Settings },
];

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-30 bg-ink-950/40 lg:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}
      <aside
        className={`fixed lg:static z-40 inset-y-0 left-0 w-64 shrink-0 bg-ink-900 text-ink-100 flex flex-col
          transition-transform duration-200 ${open ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}
      >
        <div className="flex items-center justify-center px-5 h-24 border-b border-ink-800">
          <img src="/hdb.png" alt="HDB Disbursal Intelligence" className="h-15 w-15 object-contain" />
        </div>

        <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={onClose}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-brand-600 text-white'
                    : 'text-ink-300 hover:bg-ink-800 hover:text-white'
                }`
              }
            >
              <item.icon className="h-4.5 w-4.5 shrink-0" aria-hidden />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="px-3 py-3 border-t border-ink-800 space-y-2">
          <div className="flex items-center gap-2 px-2 text-xs">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full rounded-full bg-verified-500 opacity-60 animate-ping" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-verified-500" />
            </span>
            <span className="text-ink-400 flex items-center gap-1.5">
              <Activity className="h-3.5 w-3.5" /> All systems operational
            </span>
          </div>
          <div className="flex items-center gap-2.5 px-2 pt-1">
            <div className="h-8 w-8 rounded-full bg-brand-600 flex items-center justify-center text-white text-xs font-semibold">
              SK
            </div>
            <div className="leading-tight min-w-0">
              <p className="text-sm font-medium text-white truncate">S. Kulkarni</p>
              <p className="text-[11px] text-ink-400 flex items-center gap-1">
                <ShieldAlert className="h-3 w-3" /> Verification Officer
              </p>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
