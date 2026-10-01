import { X, FileText, ShieldCheck } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { Checkpoint } from '@/types';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';

export function CheckpointDrawer({
  checkpoint,
  onClose,
}: {
  checkpoint: Checkpoint | null;
  onClose: () => void;
}) {
  if (!checkpoint) return null;
  const cp = checkpoint;

  return (
    <>
      <div className="fixed inset-0 z-40 bg-ink-950/30 animate-fade-in" onClick={onClose} aria-hidden />
      <aside
        className="fixed right-0 top-0 bottom-0 z-50 w-full max-w-xl bg-white shadow-drawer flex flex-col animate-slide-in"
        role="dialog"
        aria-label={`Checkpoint ${cp.id}: ${cp.name}`}
      >
        <div className="flex items-center justify-between px-5 h-16 border-b border-ink-200 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <span className="font-mono text-xs text-ink-400">
              {String(cp.id).padStart(2, '0')}
            </span>
            <h2 className="text-base font-semibold text-ink-900 truncate">{cp.name}</h2>
          </div>
          <button onClick={onClose} className="btn-ghost p-1.5" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-5 space-y-6">
          {/* Result */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500 mb-2">Result</h3>
            <div className="flex items-center gap-3">
              <StatusBadge status={cp.status} size="md" />
              {cp.status !== 'NOT_APPLICABLE' && (
                <div className="flex-1 max-w-[200px]">
                  <ConfidenceBar value={cp.confidence} threshold={70} />
                </div>
              )}
            </div>
            <p className="mt-2 text-sm text-ink-600">{cp.reason}</p>
          </section>

          {/* Rule */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500 mb-2">Rule</h3>
            <div className="rounded-md bg-ink-50 border border-ink-200 p-3 flex gap-2.5">
              <ShieldCheck className="h-4.5 w-4.5 text-brand-600 shrink-0 mt-0.5" />
              <p className="text-sm text-ink-700">{cp.rule}</p>
            </div>
          </section>

          {/* Extracted Values */}
          {cp.extractedFields.length > 0 && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500 mb-2">
                Extracted Values
              </h3>
              <div className="card divide-y divide-ink-100">
                {cp.extractedFields.map((f) => (
                  <div key={f.id} className="px-4 py-3">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-sm text-ink-500">{f.name}</span>
                      <span className="text-sm font-semibold text-ink-900 tabular-nums">
                        {f.value === null ? '—' : String(f.value)}
                      </span>
                    </div>
                    <div className="mt-1.5 flex items-center gap-2">
                      <span className="text-[11px] text-ink-400">
                        {f.sourceDocumentId} {f.page ? `· p.${f.page}` : ''}
                      </span>
                      <div className="flex-1 max-w-[120px]">
                        {f.confidence !== null && f.confidence !== undefined ? (
                          <ConfidenceBar value={f.confidence} size="sm" />
                        ) : (
                          <span className="text-[11px] text-ink-400 italic">No telemetry</span>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Evidence */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500 mb-2">
              Evidence
            </h3>
            {cp.evidence.length === 0 ? (
              <p className="text-sm text-ink-500">No evidence recorded for this checkpoint.</p>
            ) : (
              <div className="space-y-2">
                {cp.evidence.map((e) => (
                  <div key={e.id} className="card p-3 flex items-start gap-3 hover:bg-ink-50/50 transition-colors">
                    <FileText className="h-4.5 w-4.5 text-brand-500 shrink-0 mt-0.5" />
                    <Link
                      to={`/documents/${e.documentId}`}
                      className="flex-1 min-w-0 group"
                    >
                      <p className="text-sm font-medium text-brand-600 group-hover:text-brand-700 group-hover:underline truncate">
                        {e.label}
                      </p>
                      <p className="text-xs text-ink-500 mt-0.5">
                        {e.documentName} · Page {e.page}
                        {e.field ? ` · ${e.field}` : ''}
                      </p>
                    </Link>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>

        <div className="px-5 py-3 border-t border-ink-200 shrink-0 flex justify-end gap-2">
          <button onClick={onClose} className="btn-secondary">
            Close
          </button>
        </div>
      </aside>
    </>
  );
}
