export function ConfidenceBar({
  value,
  threshold,
  showLabel = true,
  size = 'md',
}: {
  value: number;
  threshold?: number;
  showLabel?: boolean;
  size?: 'sm' | 'md';
}) {
  const color =
    value >= 90
      ? 'bg-verified-500'
      : value >= (threshold ?? 70)
        ? 'bg-brand-500'
        : value >= 50
          ? 'bg-review-500'
          : 'bg-discrepancy-500';
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div
        className={`relative flex-1 rounded-full bg-ink-100 overflow-hidden ${
          size === 'sm' ? 'h-1.5' : 'h-2'
        }`}
        role="progressbar"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
        />
        {threshold !== undefined && threshold > 0 && (
          <div
            className="absolute top-0 bottom-0 w-px bg-ink-400/70"
            style={{ left: `${threshold}%` }}
            title={`Threshold ${threshold}%`}
          />
        )}
      </div>
      {showLabel && (
        <span className="font-mono text-xs text-ink-600 tabular-nums w-12 text-right shrink-0">
          {value.toFixed(1)}%
        </span>
      )}
    </div>
  );
}
