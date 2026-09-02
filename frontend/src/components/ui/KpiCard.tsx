import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';

export function KpiCard({
  label,
  value,
  sub,
  icon: Icon,
  tone = 'neutral',
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon?: LucideIcon;
  tone?: 'neutral' | 'verified' | 'discrepancy' | 'review' | 'info';
}) {
  const toneRing: Record<string, string> = {
    neutral: 'text-ink-500 bg-ink-100',
    verified: 'text-verified-600 bg-verified-50',
    discrepancy: 'text-discrepancy-600 bg-discrepancy-50',
    review: 'text-review-600 bg-review-50',
    info: 'text-info-600 bg-info-50',
  };
  return (
    <div className="card card-hover p-4 flex items-start gap-3">
      {Icon && (
        <div className={`rounded-md p-2 ${toneRing[tone]}`}>
          <Icon className="h-5 w-5" aria-hidden />
        </div>
      )}
      <div className="min-w-0">
        <p className="text-xs font-medium text-ink-500 uppercase tracking-wide">{label}</p>
        <p className="mt-1 text-2xl font-semibold text-ink-900 tabular-nums">{value}</p>
        {sub && <p className="mt-0.5 text-xs text-ink-500">{sub}</p>}
      </div>
    </div>
  );
}
