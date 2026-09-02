import { CheckCircle2, XCircle, AlertTriangle, MinusCircle, Loader2 } from 'lucide-react';
import type { CheckpointStatus } from '@/types';

const map: Record<
  CheckpointStatus,
  { label: string; classes: string; Icon: typeof CheckCircle2 }
> = {
  VERIFIED: {
    label: 'Verified',
    classes: 'bg-verified-50 text-verified-700 ring-verified-500/20',
    Icon: CheckCircle2,
  },
  DISCREPANCY: {
    label: 'Discrepancy',
    classes: 'bg-discrepancy-50 text-discrepancy-700 ring-discrepancy-500/20',
    Icon: XCircle,
  },
  INDETERMINATE: {
    label: 'Indeterminate',
    classes: 'bg-review-50 text-review-700 ring-review-500/20',
    Icon: AlertTriangle,
  },
  NOT_APPLICABLE: {
    label: 'Not Applicable',
    classes: 'bg-ink-100 text-ink-500 ring-ink-300/40',
    Icon: MinusCircle,
  },
  PROCESSING: {
    label: 'Processing',
    classes: 'bg-info-50 text-info-600 ring-info-500/20',
    Icon: Loader2,
  },
};

export function StatusBadge({
  status,
  size = 'sm',
}: {
  status: CheckpointStatus;
  size?: 'sm' | 'md';
}) {
  const { label, classes, Icon } = map[status];
  return (
    <span
      className={`chip ring-1 ring-inset ${classes} ${
        size === 'md' ? 'px-3 py-1.5 text-sm' : ''
      }`}
    >
      <Icon
        className={size === 'md' ? 'h-4 w-4' : 'h-3.5 w-3.5'}
        aria-hidden
        style={status === 'PROCESSING' ? { animation: 'spin 1.2s linear infinite' } : undefined}
      />
      {label}
    </span>
  );
}

export const statusLabel = (s: CheckpointStatus) => map[s].label;
