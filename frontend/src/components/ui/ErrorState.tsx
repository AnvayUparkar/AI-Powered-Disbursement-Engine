import { AlertCircle, RefreshCw } from 'lucide-react';

export function ErrorState({
  title = 'Unable to load data',
  description = 'Something went wrong while fetching this information.',
  onRetry,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="card p-10 flex flex-col items-center justify-center text-center">
      <div className="rounded-full bg-discrepancy-50 p-3 mb-3">
        <AlertCircle className="h-6 w-6 text-discrepancy-600" aria-hidden />
      </div>
      <p className="text-sm font-medium text-ink-800">{title}</p>
      <p className="mt-1 text-sm text-ink-500 max-w-sm">{description}</p>
      {onRetry && (
        <button onClick={onRetry} className="btn-secondary mt-4">
          <RefreshCw className="h-4 w-4" aria-hidden />
          Retry
        </button>
      )}
    </div>
  );
}
