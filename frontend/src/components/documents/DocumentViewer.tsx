import { useState } from 'react';
import {
  ZoomIn,
  ZoomOut,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Search,
  FileText,
  Sparkles,
  Code,
  Layers,
  Box,
} from 'lucide-react';
import type { DocumentRecord, ExtractedField } from '@/types';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';

export function DocumentViewer({ document }: { document: DocumentRecord }) {
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<'fields' | 'rawText' | 'formattedText'>('fields');
  const [searchQuery, setSearchQuery] = useState('');


  const totalPages = document.pages;

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const filteredFields = document.extractedFields.filter((f) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      f.name.toLowerCase().includes(q) ||
      String(f.value || '').toLowerCase().includes(q)
    );
  });

  return (
    <div className="card overflow-hidden flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-ink-200 bg-ink-50/50 flex-wrap">
        <div className="flex items-center gap-1">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="btn-ghost p-1.5 disabled:opacity-40"
            aria-label="Previous page"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="text-xs text-ink-600 tabular-nums px-1">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="btn-ghost p-1.5 disabled:opacity-40"
            aria-label="Next page"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>

        <div className="h-4 w-px bg-ink-200" />

        <button
          onClick={() => setZoom((z) => Math.max(0.5, z - 0.1))}
          className="btn-ghost p-1.5"
          aria-label="Zoom out"
        >
          <ZoomOut className="h-4 w-4" />
        </button>
        <span className="text-xs text-ink-600 tabular-nums w-10 text-center">
          {Math.round(zoom * 100)}%
        </span>
        <button
          onClick={() => setZoom((z) => Math.min(2, z + 0.1))}
          className="btn-ghost p-1.5"
          aria-label="Zoom in"
        >
          <ZoomIn className="h-4 w-4" />
        </button>

        <div className="h-4 w-px bg-ink-200" />

        {/* View Mode Toggle */}
        <div className="flex rounded-md bg-ink-200/60 p-0.5 text-xs">
          <button
            onClick={() => setViewMode('fields')}
            className={`px-2.5 py-1 rounded font-medium transition-colors ${
              viewMode === 'fields'
                ? 'bg-white text-ink-900 shadow-sm'
                : 'text-ink-600 hover:text-ink-900'
            }`}
          >
            <Layers className="h-3.5 w-3.5 inline-block mr-1" />
            Extracted Fields
          </button>
          <button
            onClick={() => setViewMode('rawText')}
            className={`px-2.5 py-1 rounded font-medium transition-colors ${
              viewMode === 'rawText'
                ? 'bg-white text-ink-900 shadow-sm'
                : 'text-ink-600 hover:text-ink-900'
            }`}
          >
            <Code className="h-3.5 w-3.5 inline-block mr-1" />
            Raw Text (OCR)
          </button>
          <button
            onClick={() => setViewMode('formattedText')}
            className={`px-2.5 py-1 rounded font-medium transition-colors ${
              viewMode === 'formattedText'
                ? 'bg-white text-ink-900 shadow-sm'
                : 'text-ink-600 hover:text-ink-900'
            }`}
          >
            <Sparkles className="h-3.5 w-3.5 inline-block mr-1 text-brand-600" />
            Formatted Text (LLM)
          </button>
        </div>

        <div className="ml-auto relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-ink-400" />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search fields…"
            className="input pl-8 py-1.5 text-xs w-40"
            aria-label="Search in document"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] flex-1 min-h-0">
        {/* Main Document Viewer Canvas / Preview */}
        <div className="relative bg-ink-100 flex items-center justify-center overflow-auto p-6 min-h-[400px]">
          {viewMode === 'rawText' ? (
            <div className="bg-white rounded-lg p-5 shadow-pop w-full h-full max-w-2xl overflow-y-auto font-mono text-xs text-ink-800 leading-relaxed whitespace-pre-wrap">
              <div className="flex items-center justify-between pb-3 mb-3 border-b border-ink-200 text-ink-500 font-sans">
                <span className="font-semibold text-ink-900 text-sm">
                  Raw OCR Text (Docling & RapidOCR)
                </span>
                <span>{document.pages} Pages</span>
              </div>
              {document.rawText || 'No raw OCR text available.'}
            </div>
          ) : viewMode === 'formattedText' ? (
            <div className="bg-white rounded-lg p-5 shadow-pop w-full h-full max-w-2xl overflow-y-auto font-mono text-xs text-ink-800 leading-relaxed whitespace-pre-wrap">
              <div className="flex items-center justify-between pb-3 mb-3 border-b border-ink-200 text-ink-500 font-sans">
                <span className="font-semibold text-ink-900 text-sm">
                  Formatted Text (OpenRouter LLM)
                </span>
                <span className="text-xs bg-brand-50 text-brand-700 px-2 py-0.5 rounded font-medium">Canonical JSON</span>
              </div>
              {document.formattedText || (
                document.extractedFields
                  .filter((f) => f.source === 'OPENROUTER_LLM' || f.type === 'key_value')
                  .map((f) => `${f.name}: ${f.value}`)
                  .join('\n')
              ) || 'No LLM formatted text available.'}
            </div>
          ) : (

            <div
              className="bg-white shadow-pop rounded-sm flex items-center justify-center transition-transform relative"
              style={{
                width: `${340 * zoom}px`,
                height: `${440 * zoom}px`,
              }}
            >
              <div className="text-center text-ink-300 p-8 w-full h-full flex flex-col items-center justify-center">
                <FileText className="h-12 w-12 mx-auto mb-2 text-brand-500/80" />
                <p className="text-sm font-semibold text-ink-800">{document.name}</p>
                <p className="text-xs text-ink-500 mt-1">
                  Page {page} of {totalPages}
                </p>

                {document.vlmUsed && (
                  <div className="mt-3 chip bg-review-50 text-review-700 border border-review-200 text-xs">
                    <Sparkles className="h-3 w-3 mr-1" />
                    VLM Fallback Applied (Handwriting Verified)
                  </div>
                )}

                <p className="text-[11px] text-ink-400 mt-3 max-w-[240px] leading-relaxed">
                  Processed via Node 2 IDP Engine (Docling + RapidOCR PP-OCRv6). Click small arrows in side panel to inspect text coordinates & provenance.
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Side Panel: Extracted Fields with Expandable Arrow Accordion */}
        <div className="border-t lg:border-t-0 lg:border-l border-ink-200 overflow-y-auto flex flex-col">
          <div className="px-4 py-3 border-b border-ink-100 flex items-center justify-between bg-white shrink-0">
            <div>
              <h3 className="text-sm font-semibold text-ink-800">
                Extracted Text & Fields
              </h3>
              <p className="text-xs text-ink-500 mt-0.5">
                {filteredFields.length} extracted items (Click arrow to expand)
              </p>
            </div>
            {document.vlmUsed && (
              <span className="chip bg-review-50 text-review-700 text-[10px]">
                VLM Active
              </span>
            )}
          </div>

          {filteredFields.length === 0 ? (
            <p className="px-4 py-6 text-sm text-ink-500">No extracted fields match search.</p>
          ) : (
            <div className="divide-y divide-ink-100 overflow-y-auto flex-1">
              {filteredFields.map((f) => {
                const isExpanded = expandedIds.has(f.id);
                return (
                  <div
                    key={f.id}
                    className="hover:bg-ink-50/50 transition-colors transition-all"
                  >
                    {/* Header Row with Arrow Icon */}
                    <button
                      onClick={() => toggleExpand(f.id)}
                      className="w-full text-left px-4 py-3 flex items-start gap-2 focus:outline-none"
                    >
                      <div className="mt-0.5 text-ink-400 hover:text-ink-600 transition-transform">
                        {isExpanded ? (
                          <ChevronDown className="h-4 w-4 text-brand-600" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )}
                      </div>

                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs font-medium text-ink-600 truncate">
                            {f.name}
                          </span>
                          {f.source && (
                            <span
                              className={`text-[10px] px-1.5 py-0.5 rounded font-mono uppercase font-semibold shrink-0 ${
                                f.source === 'vlm'
                                  ? 'bg-review-50 text-review-700 border border-review-200'
                                  : f.source === 'docling' || f.source === 'OCR' || f.source === 'ocr'
                                  ? 'bg-info-50 text-info-700 border border-info-200'
                                  : f.source === 'OPENROUTER_LLM' || f.source === 'llm'
                                  ? 'bg-purple-50 text-purple-700 border border-purple-200'
                                  : 'bg-ink-100 text-ink-600'
                              }`}
                            >
                              {f.source === 'OPENROUTER_LLM' ? 'LLM' : f.source}
                            </span>
                          )}
                        </div>

                        <p className="text-sm font-semibold text-ink-900 mt-0.5 truncate">
                          {f.value === null ? '—' : String(f.value)}
                        </p>

                        <div className="mt-1 flex items-center gap-2">
                          <span className="text-[11px] text-ink-400">
                            {f.page ? `Page ${f.page}` : 'p.1'}
                          </span>
                          <div className="flex-1 max-w-[120px]">
                            <ConfidenceBar value={f.confidence} size="sm" />
                          </div>
                        </div>
                      </div>
                    </button>

                    {/* Expanded Detail Panel */}
                    {isExpanded && (
                      <div className="px-4 pb-3 pt-1 ml-6 mr-2 border-l-2 border-brand-500 bg-brand-50/20 text-xs space-y-2 rounded-r-md">
                        <div>
                          <span className="font-semibold text-ink-700">Full Extracted Text:</span>
                          <p className="font-mono text-ink-900 bg-white p-2 rounded border border-ink-200 mt-1 select-all">
                            {String(f.value || '')}
                          </p>
                        </div>

                        {f.ocrOriginal && (
                          <div className="text-amber-800 bg-amber-50 p-2 rounded border border-amber-200">
                            <span className="font-semibold">Original OCR Output (Before VLM Correction):</span>
                            <p className="font-mono text-xs mt-0.5">{f.ocrOriginal}</p>
                          </div>
                        )}

                        {f.bbox && (
                          <div className="flex items-center gap-1.5 text-ink-500 font-mono text-[11px]">
                            <Box className="h-3.5 w-3.5 text-ink-400" />
                            <span>BBox: [{f.bbox.map((n) => n.toFixed(3)).join(', ')}]</span>
                          </div>
                        )}

                        {f.type === 'table' && f.rows && (
                          <div className="mt-2 overflow-x-auto">
                            <span className="font-semibold text-ink-700 block mb-1">Table Grid:</span>
                            <table className="w-full text-[11px] border border-ink-200 bg-white rounded">
                              {f.headers && f.headers.length > 0 && (
                                <thead className="bg-ink-50 border-b border-ink-200">
                                  <tr>
                                    {f.headers.map((h, hIdx) => (
                                      <th key={hIdx} className="p-1 text-left font-semibold text-ink-700">{h}</th>
                                    ))}
                                  </tr>
                                </thead>
                              )}
                              <tbody>
                                {f.rows.map((row, rIdx) => (
                                  <tr key={rIdx} className="border-b border-ink-100">
                                    {row.map((cell, cIdx) => (
                                      <td key={cIdx} className="p-1 text-ink-800">{cell}</td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
