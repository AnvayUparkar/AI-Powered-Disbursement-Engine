import { useState, useRef, useEffect, useMemo } from 'react';
import {
  ZoomIn,
  ZoomOut,
  ChevronLeft,
  ChevronRight,
  Sparkles,
  FileText,
  Maximize2,
  Minimize2,
  RotateCcw,
  Copy,
  Check,
} from 'lucide-react';
import type { DocumentRecord, ExtractedField } from '@/types';

/**
 * Clean, high-contrast syntax highlighter for JSON using the app's Inter font-sans
 * and cohesive slate/ink color palette.
 */
function renderHighlightedJson(json: string) {
  const regex = /("((\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*)"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?|[{}\[\],])/g;
  const parts = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let keyIdx = 0;

  while ((match = regex.exec(json)) !== null) {
    const offset = match.index;
    if (offset > lastIndex) {
      parts.push(<span key={keyIdx++} className="text-ink-400">{json.slice(lastIndex, offset)}</span>);
    }
    const token = match[0];

    if (/^"/.test(token)) {
      if (/:$/.test(token)) {
        // Key
        const keyText = token.slice(0, -1);
        parts.push(
          <span key={keyIdx++}>
            <span className="text-ink-900 font-semibold">{keyText}</span>
            <span className="text-ink-400">:</span>
          </span>
        );
        lastIndex = regex.lastIndex;
        continue;
      } else {
        // String value
        parts.push(
          <span key={keyIdx++} className="text-ink-800 font-normal">
            {token}
          </span>
        );
        lastIndex = regex.lastIndex;
        continue;
      }
    } else if (/true|false/.test(token)) {
      parts.push(
        <span key={keyIdx++} className="text-brand-700 font-medium">
          {token}
        </span>
      );
    } else if (/null/.test(token)) {
      parts.push(
        <span key={keyIdx++} className="text-ink-400 italic">
          {token}
        </span>
      );
    } else if (/[{}\[\],]/.test(token)) {
      parts.push(
        <span key={keyIdx++} className="text-ink-400">
          {token}
        </span>
      );
    } else {
      // Numbers
      parts.push(
        <span key={keyIdx++} className="text-brand-700 font-medium">
          {token}
        </span>
      );
    }

    lastIndex = regex.lastIndex;
  }

  if (lastIndex < json.length) {
    parts.push(<span key={keyIdx++} className="text-ink-400">{json.slice(lastIndex)}</span>);
  }

  return parts;
}

export function DocumentViewer({ document }: { document: DocumentRecord }) {
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [imageError, setImageError] = useState(false);
  const [imageLoading, setImageLoading] = useState(true);
  const [copiedJson, setCopiedJson] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<HTMLDivElement>(null);

  const maxFieldPage = (document.extractedFields || []).reduce((max, f) => Math.max(max, f.page || 1), 1);
  const rawPages = Array.isArray(document.pages)
    ? (document.pages as any[]).length
    : typeof document.pages === 'number'
    ? document.pages
    : 1;
  const totalPages = Math.max(1, rawPages, maxFieldPage);

  useEffect(() => {
    const onFullscreenChange = () => setIsFullscreen(window.document.fullscreenElement === viewerRef.current);
    window.document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => window.document.removeEventListener('fullscreenchange', onFullscreenChange);
  }, []);

  const toggleFullscreen = () => {
    if (!viewerRef.current) return;
    if (window.document.fullscreenElement) {
      window.document.exitFullscreen().catch(() => {});
    } else {
      viewerRef.current.requestFullscreen().catch(() => {});
    }
  };

  const imageUrl = `/api/documents/preview/${encodeURIComponent(document.caseId)}/${encodeURIComponent(document.name)}?page=${page}&format=image`;

  useEffect(() => {
    setImageError(false);
    setImageLoading(true);
  }, [page, document.caseId, document.name]);

  // Compute canonical structured JSON string from LLM formatted output or fallback fields
  const canonicalJsonString = useMemo(() => {
    if (document.formattedText && document.formattedText.trim().startsWith('{')) {
      try {
        const parsed = JSON.parse(document.formattedText);
        return JSON.stringify(parsed, null, 2);
      } catch {
        return document.formattedText;
      }
    }
    const result: Record<string, ExtractedField['value']> = { document_type: document.type };
    for (const f of document.extractedFields || []) {
      if ((f.type ?? 'key_value') === 'key_value' && f.value !== null && f.value !== '') {
        result[f.name] = f.value;
      }
    }
    return JSON.stringify(result, null, 2);
  }, [document.formattedText, document.type, document.extractedFields]);

  const handleCopyJson = () => {
    navigator.clipboard.writeText(canonicalJsonString);
    setCopiedJson(true);
    setTimeout(() => setCopiedJson(false), 2000);
  };

  return (
    <div
      ref={viewerRef}
      className={`card overflow-hidden flex flex-col h-full ${isFullscreen ? 'bg-white w-screen h-screen' : ''}`}
    >
      {/* Primary Toolbar */}
      <div className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-ink-200 bg-ink-50/50 flex-wrap">
        {/* Left Toolbar: Page & Zoom Controls */}
        <div className="flex items-center gap-2">
          {/* Pagination */}
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="btn-ghost p-1.5 disabled:opacity-40"
              aria-label="Previous page"
              title="Previous page"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="text-xs text-ink-600 tabular-nums px-1 font-mono">
              Page {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="btn-ghost p-1.5 disabled:opacity-40"
              aria-label="Next page"
              title="Next page"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>

          <div className="h-4 w-px bg-ink-200" />

          {/* Zoom Controls */}
          <button
            onClick={() => setZoom((z) => Math.max(0.4, Number((z - 0.1).toFixed(1))))}
            className="btn-ghost p-1.5"
            aria-label="Zoom out"
            title="Zoom out"
          >
            <ZoomOut className="h-4 w-4" />
          </button>
          <span className="text-xs text-ink-600 tabular-nums w-12 text-center font-mono">
            {Math.round(zoom * 100)}%
          </span>
          <button
            onClick={() => setZoom((z) => Math.min(2.5, Number((z + 0.1).toFixed(1))))}
            className="btn-ghost p-1.5"
            aria-label="Zoom in"
            title="Zoom in"
          >
            <ZoomIn className="h-4 w-4" />
          </button>
          <button
            onClick={() => setZoom(1)}
            className="btn-ghost p-1.5 text-xs text-ink-500 hover:text-ink-800"
            title="Reset Zoom"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={toggleFullscreen}
            className="btn-ghost p-1.5 text-xs text-ink-500 hover:text-ink-800"
            title={isFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
          >
            {isFullscreen ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </button>
        </div>

        {/* Right Toolbar: Quick Badge */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-ink-500 font-mono">
            {document.type}
          </span>
        </div>
      </div>

      {/* Main Split Content Area */}
      <div className="grid grid-cols-1 lg:grid-cols-2 flex-1 min-h-0 divide-y lg:divide-y-0 lg:divide-x divide-ink-200">
        {/* Left Side: Clean Document View */}
        <div className="relative bg-ink-100/70 flex items-start justify-center overflow-auto p-4 min-h-[500px]">
          <div
            ref={containerRef}
            className="relative inline-block transition-transform duration-100 origin-top shadow-md rounded bg-white"
            style={{
              transform: `scale(${zoom})`,
              transformOrigin: 'top center',
            }}
          >
            {!imageError ? (
              <div className="relative">
                <img
                  src={imageUrl}
                  alt={`Document page ${page}`}
                  className="max-w-none block rounded select-none pointer-events-auto"
                  style={{ minWidth: '500px', width: '600px', height: 'auto' }}
                  onLoad={() => {
                    setImageLoading(false);
                    setImageError(false);
                  }}
                  onError={() => {
                    setImageError(true);
                    setImageLoading(false);
                  }}
                />
                {imageLoading && (
                  <div className="absolute inset-0 bg-ink-50/80 flex flex-col items-center justify-center text-ink-500">
                    <div className="w-8 h-8 border-2 border-brand-500 border-t-transparent rounded-full animate-spin mb-2" />
                    <span className="text-xs font-medium">Loading document page {page}...</span>
                  </div>
                )}
              </div>
            ) : (
              /* Fallback Canvas when preview image endpoint is unavailable */
              <div
                className="bg-white border border-ink-300 rounded shadow-md relative"
                style={{ width: '600px', height: '850px' }}
              >
                <div className="absolute inset-0 bg-[radial-gradient(#e2e8f0_1px,transparent_1px)] [background-size:16px_16px] opacity-60" />
                <div className="p-8 text-center text-ink-400 flex flex-col items-center justify-center h-full">
                  <FileText className="h-16 w-16 text-ink-300 mb-3" />
                  <p className="text-base font-semibold text-ink-800">{document.name}</p>
                  <p className="text-xs text-ink-500 mt-1">Page {page} of {totalPages}</p>
                  <div className="mt-3 px-3 py-1 bg-amber-50 border border-amber-200 text-amber-800 rounded text-xs">
                    Document image preview not rendered yet.
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right Side: LLM JSON Output */}
        <div className="flex flex-col bg-white overflow-hidden min-h-[500px]">
          {/* Header */}
          <div className="px-4 py-3 border-b border-ink-200 flex items-center justify-between shrink-0 bg-ink-50/60">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-brand-600" />
              <h3 className="text-sm font-semibold text-ink-900">
                LLM JSON Output
              </h3>
              <span className="text-[11px] bg-brand-50 text-brand-700 px-2 py-0.5 rounded font-medium border border-brand-200">
                Canonical JSON
              </span>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleCopyJson}
                className="btn-secondary px-2.5 py-1 text-xs flex items-center gap-1.5"
                title="Copy Canonical JSON"
              >
                {copiedJson ? (
                  <>
                    <Check className="h-3.5 w-3.5 text-emerald-600" />
                    <span className="text-emerald-700 font-medium">Copied</span>
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5 text-ink-600" />
                    <span>Copy JSON</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {/* JSON Content Area with Clean Light Theme */}
          <div className="flex-1 overflow-auto p-4 bg-ink-50/40">
            <pre className="font-sans text-xs text-ink-800 bg-white p-5 rounded-lg border border-ink-200 leading-6 overflow-x-auto min-h-full select-all shadow-card whitespace-pre">
              {renderHighlightedJson(canonicalJsonString)}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
}
