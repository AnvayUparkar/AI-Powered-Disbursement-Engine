import { ScanText } from 'lucide-react';
import type { OcrEngineInfo } from '@/types';

/** Chip naming the OCR engine that produced a document's text. Renders only for LightOnOCR (via
 * LiteLLM), since Docling + RapidOCR is the default and already described by the pipeline steps. */
export function OcrEngineBadge({ info, className = '' }: { info?: OcrEngineInfo | null; className?: string }) {
  if (!info || info.engine !== 'lightonocr') return null;
  const processed = info.pagesProcessed ?? 0;
  const failed = info.pagesFailed ?? 0;
  const title =
    `${info.model || 'LightOnOCR'} via LiteLLM: ${processed}/${processed + failed} scanned page(s) read` +
    (failed ? `, ${failed} failed` : '') +
    (info.seconds != null ? ` in ${info.seconds.toFixed(1)}s` : '');
  return (
    <span className={`chip bg-info-50 text-info-600 ${className}`} title={title}>
      <ScanText className="h-3.5 w-3.5" /> LightOnOCR via LiteLLM
      {failed > 0 && <span className="text-review-700">· {failed} page{failed > 1 ? 's' : ''} failed</span>}
    </span>
  );
}
