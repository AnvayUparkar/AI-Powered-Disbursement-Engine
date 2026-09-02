export function BarChart({
  data,
  max,
  unit = '',
  height = 160,
}: {
  data: { label: string; value: number }[];
  max?: number;
  unit?: string;
  height?: number;
}) {
  const m = max ?? Math.max(...data.map((d) => d.value), 1);
  return (
    <div className="flex items-end gap-2" style={{ height }}>
      {data.map((d, i) => (
        <div key={i} className="flex-1 flex flex-col items-center gap-1.5 min-w-0">
          <div className="w-full flex-1 flex items-end">
            <div
              className="w-full rounded-t bg-brand-500/80 hover:bg-brand-600 transition-colors"
              style={{ height: `${(d.value / m) * 100}%` }}
              title={`${d.label}: ${d.value}${unit}`}
            />
          </div>
          <span className="text-[10px] text-ink-500 truncate w-full text-center">
            {d.label}
          </span>
        </div>
      ))}
    </div>
  );
}

export function LineChart({
  data,
  height = 160,
  unit = '',
}: {
  data: { label: string; value: number }[];
  height?: number;
  unit?: string;
}) {
  const w = 100;
  const h = 100;
  const max = Math.max(...data.map((d) => d.value), 1);
  const min = Math.min(...data.map((d) => d.value), 0);
  const range = max - min || 1;
  const step = data.length > 1 ? w / (data.length - 1) : 0;
  const pts = data.map((d, i) => {
    const x = i * step;
    const y = h - ((d.value - min) / range) * (h - 8) - 4;
    return [x, y] as const;
  });
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0]},${p[1]}`).join(' ');
  const area = `${path} L${w},${h} L0,${h} Z`;
  return (
    <div style={{ height }} className="w-full">
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="w-full h-full">
        <defs>
          <linearGradient id="lineFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1E6BFF" stopOpacity="0.18" />
            <stop offset="100%" stopColor="#1E6BFF" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#lineFill)" />
        <path d={path} fill="none" stroke="#1E6BFF" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        {pts.map((p, i) => (
          <circle key={i} cx={p[0]} cy={p[1]} r="1.4" fill="#1E6BFF" vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
      <div className="flex justify-between mt-1">
        {data.map((d, i) => (
          <span key={i} className="text-[10px] text-ink-500">
            {d.label}
          </span>
        ))}
      </div>
      <span className="sr-only">{unit}</span>
    </div>
  );
}

export function DualBarChart({
  data,
  height = 160,
}: {
  data: { label: string; a: number; b: number }[];
  height?: number;
}) {
  const max = Math.max(...data.flatMap((d) => [d.a, d.b]), 1);
  return (
    <div className="flex items-end gap-2" style={{ height }}>
      {data.map((d, i) => (
        <div key={i} className="flex-1 flex flex-col items-center gap-1.5 min-w-0">
          <div className="w-full flex-1 flex items-end justify-center gap-1">
            <div
              className="w-1/2 rounded-t bg-brand-500/80 hover:bg-brand-600 transition-colors"
              style={{ height: `${(d.a / max) * 100}%` }}
              title={`${d.label} created: ${d.a}`}
            />
            <div
              className="w-1/2 rounded-t bg-verified-500/80 hover:bg-verified-600 transition-colors"
              style={{ height: `${(d.b / max) * 100}%` }}
              title={`${d.label} resolved: ${d.b}`}
            />
          </div>
          <span className="text-[10px] text-ink-500 truncate w-full text-center">
            {d.label}
          </span>
        </div>
      ))}
    </div>
  );
}
