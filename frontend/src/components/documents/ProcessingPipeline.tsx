import { CheckCircle2, XCircle, AlertTriangle, Loader2, Info } from 'lucide-react';
import type { ProcessingStep } from '@/types';

const iconFor = (status: ProcessingStep['status']) => {
  switch (status) {
    case 'COMPLETED':
      return <CheckCircle2 className="h-4 w-4 text-verified-600" />;
    case 'FAILED':
      return <XCircle className="h-4 w-4 text-discrepancy-600" />;
    case 'WARNING':
      return <AlertTriangle className="h-4 w-4 text-review-600" />;
    case 'PROCESSING':
      return <Loader2 className="h-4 w-4 text-info-600 animate-spin" />;
    case 'SKIPPED':
      return <Info className="h-4 w-4 text-ink-400" />;
  }
};

export function ProcessingPipeline({ steps }: { steps: ProcessingStep[] }) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-ink-800">Processing Pipeline</h3>
      </div>
      <ol className="space-y-0">
        {steps.map((s, i) => (
          <li key={s.id} className="flex gap-3">
            <div className="flex flex-col items-center">
              <div className="rounded-full p-1 bg-white ring-1 ring-ink-200">{iconFor(s.status)}</div>
              {i < steps.length - 1 && <div className="w-px flex-1 bg-ink-200 my-1" />}
            </div>
            <div className="flex-1 pb-4">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium text-ink-800">{s.component}</p>
                <span className="font-mono text-[11px] text-ink-400">{s.startedAt}</span>
              </div>
              <p className="text-xs text-ink-500 mt-0.5">{s.detail}</p>
              {s.confidence !== undefined && (
                <p className="text-[11px] text-ink-400 mt-1">
                  Confidence: {s.confidence.toFixed(1)}%
                </p>
              )}
            </div>
          </li>
        ))}
      </ol>
      <div className="mt-2 pt-3 border-t border-ink-100 text-[11px] text-ink-500 leading-relaxed">
        <span className="font-medium text-ink-600">VLM</span> = Extraction / visual interpretation.{' '}
        <span className="font-medium text-ink-600">Rule Engine</span> = Final validation.
      </div>
    </div>
  );
}
