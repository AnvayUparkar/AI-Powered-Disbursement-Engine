import { Timer } from 'lucide-react';
import type { DocumentTiming, RunTimingSummary } from '@/types';

const STAGE_LABELS: Record<string, string> = {
  download: 'Download',
  preprocess: 'Validate & count pages',
  scan_cleanup: 'Scan cleanup (deskew, denoise)',
  docling: 'Docling OCR & layout',
  lightonocr: 'LightOnOCR (LiteLLM)',
  page_images: 'Render page images',
  vlm: 'VLM fallback',
  comb_grid: 'Comb-box recovery',
  serialize: 'Serialize document',
  llm_field_extraction: 'LLM field extraction (LiteLLM)',
  field_locations: 'Locate fields on page',
  parse: 'Fast-path parse',
  save: 'Save result',
};

const NODE_LABELS: Record<string, string> = {
  fetch_los: 'Fetch LOS record',
  fetch_documents: 'Fetch documents',
  idp_scan: 'IDP scan (OCR)',
  llm_structure: 'LLM structuring',
  check_parallel: 'Verification checks',
  compile_report: 'Compile report',
  generate_scorecard: 'Scorecard',
  push_results: 'Push results',
};

const MODEL_LABELS: Record<string, string> = {
  layout: 'layout',
  ocr: 'RapidOCR',
  table_structure: 'TableFormer',
  page_parse: 'page parse',
  reading_order: 'reading order',
};

const SOURCE_LABELS: Record<string, string> = {
  idp: 'idp pod',
  cache: 'cached',
  xml_local: 'XML (local)',
  pyhanko: 'signature check',
  failed: 'failed',
};

function fmt(seconds: number): string {
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  if (seconds >= 10) return `${seconds.toFixed(0)}s`;
  return `${seconds.toFixed(1)}s`;
}

/** One labelled row with a bar scaled to `scale` seconds. */
function Bar({ label, seconds, scale, note }: { label: string; seconds: number; scale: number; note?: string }) {
  const pct = scale > 0 ? Math.max(1, Math.min(100, (seconds / scale) * 100)) : 0;
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 items-baseline">
      <span className="text-xs text-ink-700 truncate" title={label}>{label}</span>
      <span className="font-mono text-[11px] text-ink-600 tabular-nums">{fmt(seconds)}</span>
      <span className="col-span-2 h-1.5 rounded-full bg-ink-100 overflow-hidden">
        <span className="block h-full rounded-full bg-brand-500" style={{ width: `${pct}%` }} />
      </span>
      {note && <span className="col-span-2 text-[11px] text-ink-400 -mt-0.5">{note}</span>}
    </li>
  );
}

function Header({ title, total, sub }: { title: string; total: number; sub?: string }) {
  return (
    <div className="flex items-start justify-between gap-2 mb-3">
      <div>
        <h3 className="text-sm font-semibold text-ink-800 flex items-center gap-1.5">
          <Timer className="h-4 w-4 text-ink-500" /> {title}
        </h3>
        {sub && <p className="text-[11px] text-ink-400 mt-0.5">{sub}</p>}
      </div>
      <span className="font-mono text-sm font-semibold text-ink-900 tabular-nums">{fmt(total)}</span>
    </div>
  );
}

/** Where one document's time went inside the idp pod. */
export function DocumentTimingPanel({ timing }: { timing?: DocumentTiming | null }) {
  if (!timing || timing.stages.length === 0) return null;
  const models = timing.doclingModels || {};
  const modelNote = Object.keys(MODEL_LABELS)
    .filter((k) => models[k] != null)
    .map((k) => `${MODEL_LABELS[k]} ${fmt(models[k])}`)
    .join(' · ');
  return (
    <div className="card p-4">
      <Header title="Time breakdown" total={timing.totalSeconds} sub="Time spent in the idp pod, per stage" />
      <ul className="space-y-2.5">
        {timing.stages.map((s) => (
          <Bar
            key={s.stage}
            label={STAGE_LABELS[s.stage] || s.stage}
            seconds={s.seconds}
            scale={timing.totalSeconds}
            note={s.stage === 'docling' && modelNote ? modelNote : undefined}
          />
        ))}
      </ul>
    </div>
  );
}

/** Where the last pipeline run's time went: by service, by step, and the slowest documents. */
export function RunTimingPanel({ summary }: { summary?: RunTimingSummary | null }) {
  if (!summary) return null;
  const maxService = Math.max(0, ...summary.services.map((s) => s.seconds));
  const finished = new Date(summary.finished_at);
  const docs = summary.documents.slice(0, 6);
  return (
    <div className="card p-4">
      <Header
        title="Time breakdown — last run"
        total={summary.total_seconds}
        sub={`${summary.entry} · finished ${isNaN(finished.getTime()) ? summary.finished_at : finished.toLocaleString()}`}
      />

      {summary.services.length > 0 && (
        <section className="mb-4">
          <h4 className="text-[11px] font-semibold uppercase tracking-wider text-ink-500 mb-2">By service</h4>
          <ul className="space-y-2.5">
            {summary.services.map((s) => (
              <Bar key={s.service} label={s.service} seconds={s.seconds} scale={maxService} />
            ))}
          </ul>
          <p className="text-[11px] text-ink-400 mt-2">
            Busy time. Documents are processed several at once, so these can add up to more than the run's total.
          </p>
        </section>
      )}

      {summary.nodes.length > 0 && (
        <section className="mb-4">
          <h4 className="text-[11px] font-semibold uppercase tracking-wider text-ink-500 mb-2">By pipeline step</h4>
          <ul className="space-y-2.5">
            {summary.nodes.map((n) => (
              <Bar key={n.node} label={NODE_LABELS[n.node] || n.node} seconds={n.seconds} scale={summary.total_seconds} />
            ))}
          </ul>
        </section>
      )}

      {docs.length > 0 && (
        <section>
          <h4 className="text-[11px] font-semibold uppercase tracking-wider text-ink-500 mb-2">Slowest documents</h4>
          <ul className="space-y-1.5">
            {docs.map((d) => {
              const top = Object.entries(d.idp_stages).sort((a, b) => b[1] - a[1])[0];
              return (
                <li key={d.doc_key} className="text-xs">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-ink-700 truncate">{d.doc_key}</span>
                    <span className="font-mono text-[11px] text-ink-600 tabular-nums">{fmt(d.seconds)}</span>
                  </div>
                  <p className="text-[11px] text-ink-400">
                    {SOURCE_LABELS[d.source] || d.source}
                    {top ? ` · most in ${STAGE_LABELS[top[0]] || top[0]} (${fmt(top[1])})` : ''}
                    {d.wait_seconds > 0.5 ? ` · waited ${fmt(d.wait_seconds)} for idp` : ''}
                  </p>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {summary.llm_calls.count > 0 && (
        <p className="text-[11px] text-ink-500 mt-3 pt-3 border-t border-ink-100">
          {summary.llm_calls.count} LLM call{summary.llm_calls.count === 1 ? '' : 's'} from the{' '}
          {summary.entry === 'celery' ? 'worker' : 'api'} pod
          {summary.llm_calls.failed > 0 ? `, ${summary.llm_calls.failed} failed` : ''}.
        </p>
      )}
    </div>
  );
}
