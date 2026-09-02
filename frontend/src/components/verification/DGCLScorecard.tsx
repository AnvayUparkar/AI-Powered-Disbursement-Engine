import { useState } from 'react';
import {
  CheckCircle2,
  XCircle,
  AlertTriangle,
  MinusCircle,
  Loader2,
  ChevronDown,
  FileText,
  ArrowRight,
} from 'lucide-react';
import type { Checkpoint } from '@/types';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';

const iconFor = (status: Checkpoint['status']) => {
  switch (status) {
    case 'VERIFIED':
      return <CheckCircle2 className="h-5 w-5 text-verified-600" />;
    case 'DISCREPANCY':
      return <XCircle className="h-5 w-5 text-discrepancy-600" />;
    case 'INDETERMINATE':
      return <AlertTriangle className="h-5 w-5 text-review-600" />;
    case 'NOT_APPLICABLE':
      return <MinusCircle className="h-5 w-5 text-ink-400" />;
    case 'PROCESSING':
      return <Loader2 className="h-5 w-5 text-info-600 animate-spin" />;
  }
};

export function DGCLScorecard({
  checkpoints,
  onCheckpointClick,
}: {
  checkpoints: Checkpoint[];
  onCheckpointClick?: (cp: Checkpoint) => void;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);

  const toggle = (id: number) => setExpanded((p) => (p === id ? null : id));

  return (
    <div className="card divide-y divide-ink-100">
      {checkpoints.map((cp) => {
        const isOpen = expanded === cp.id;
        const na = cp.status === 'NOT_APPLICABLE';
        return (
          <div key={cp.id} className={na ? 'opacity-60' : ''}>
            <div className="flex items-start gap-3 px-4 py-3.5">
              <span className="font-mono text-xs text-ink-400 mt-0.5 w-6 shrink-0">
                {String(cp.id).padStart(2, '0')}
              </span>
              <button
                onClick={() => toggle(cp.id)}
                className="mt-0.5 shrink-0"
                aria-label={isOpen ? 'Collapse' : 'Expand'}
              >
                {iconFor(cp.status)}
              </button>
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <button
                    onClick={() => onCheckpointClick?.(cp)}
                    className="text-sm font-medium text-ink-800 hover:text-brand-600 text-left"
                  >
                    {cp.name}
                  </button>
                  <StatusBadge status={cp.status} />
                </div>
                {!na && (
                  <div className="mt-1.5 max-w-xs">
                    <ConfidenceBar value={cp.confidence} threshold={70} size="sm" />
                  </div>
                )}
                {isOpen && (
                  <div className="mt-3 space-y-3 animate-fade-in">
                    <p className="text-sm text-ink-600">{cp.reason}</p>
                    {cp.extractedFields.length > 0 && (
                      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5">
                        {cp.extractedFields.map((f) => (
                          <div key={f.id} className="flex justify-between gap-3 text-sm">
                            <dt className="text-ink-500">{f.name}</dt>
                            <dd className="font-medium text-ink-800 text-right tabular-nums">
                              {f.value === null ? '—' : String(f.value)}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    )}
                    <div className="flex flex-wrap items-center gap-3 text-xs text-ink-500">
                      <span className="inline-flex items-center gap-1">
                        <FileText className="h-3.5 w-3.5" />
                        {cp.evidence.length} {cp.evidence.length === 1 ? 'piece' : 'pieces'} of evidence
                      </span>
                      {cp.validation && (
                        <span
                          className={`chip ring-1 ring-inset ${
                            cp.validation.result === 'MATCH'
                              ? 'bg-verified-50 text-verified-700 ring-verified-500/20'
                              : cp.validation.result === 'MISMATCH'
                                ? 'bg-discrepancy-50 text-discrepancy-700 ring-discrepancy-500/20'
                                : 'bg-review-50 text-review-700 ring-review-500/20'
                          }`}
                        >
                          {cp.validation.left} {cp.validation.result === 'MATCH' ? '=' : cp.validation.result === 'MISMATCH' ? '≠' : '?'} {cp.validation.right}
                        </span>
                      )}
                      <button
                        onClick={() => onCheckpointClick?.(cp)}
                        className="ml-auto inline-flex items-center gap-1 text-brand-600 hover:text-brand-700 font-medium"
                      >
                        View details <ArrowRight className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                )}
              </div>
              <button
                onClick={() => toggle(cp.id)}
                className="shrink-0 p-1 text-ink-400 hover:text-ink-600"
                aria-label={isOpen ? 'Collapse' : 'Expand'}
              >
                <ChevronDown
                  className={`h-4 w-4 transition-transform ${isOpen ? 'rotate-180' : ''}`}
                />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
