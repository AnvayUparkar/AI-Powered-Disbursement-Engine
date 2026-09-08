import { useState, useRef, useEffect } from 'react';
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
  Eye,
  EyeOff,
  AlertCircle,
  CheckCircle2,
  HelpCircle,
  Tag,
  Crosshair,
  Maximize2,
  Copy,
  Check,
} from 'lucide-react';
import type { DocumentRecord, ExtractedField, OCRToken } from '@/types';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';

const FIELD_PALETTE = [
  { border: 'border-blue-500', bg: 'bg-blue-500/20', hoverBg: 'hover:bg-blue-500/30', text: 'text-blue-700', badge: 'bg-blue-100 text-blue-800 border-blue-300', hex: '#3b82f6' },
  { border: 'border-emerald-500', bg: 'bg-emerald-500/20', hoverBg: 'hover:bg-emerald-500/30', text: 'text-emerald-700', badge: 'bg-emerald-100 text-emerald-800 border-emerald-300', hex: '#10b981' },
  { border: 'border-purple-500', bg: 'bg-purple-500/20', hoverBg: 'hover:bg-purple-500/30', text: 'text-purple-700', badge: 'bg-purple-100 text-purple-800 border-purple-300', hex: '#8b5cf6' },
  { border: 'border-amber-500', bg: 'bg-amber-500/20', hoverBg: 'hover:bg-amber-500/30', text: 'text-amber-700', badge: 'bg-amber-100 text-amber-800 border-amber-300', hex: '#f59e0b' },
  { border: 'border-rose-500', bg: 'bg-rose-500/20', hoverBg: 'hover:bg-rose-500/30', text: 'text-rose-700', badge: 'bg-rose-100 text-rose-800 border-rose-300', hex: '#f43f5e' },
  { border: 'border-cyan-500', bg: 'bg-cyan-500/20', hoverBg: 'hover:bg-cyan-500/30', text: 'text-cyan-700', badge: 'bg-cyan-100 text-cyan-800 border-cyan-300', hex: '#06b6d4' },
  { border: 'border-indigo-500', bg: 'bg-indigo-500/20', hoverBg: 'hover:bg-indigo-500/30', text: 'text-indigo-700', badge: 'bg-indigo-100 text-indigo-800 border-indigo-300', hex: '#6366f1' },
  { border: 'border-teal-500', bg: 'bg-teal-500/20', hoverBg: 'hover:bg-teal-500/30', text: 'text-teal-700', badge: 'bg-teal-100 text-teal-800 border-teal-300', hex: '#14b8a6' },
];

function getFieldColor(name: string, index: number) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash << 5) - hash + name.charCodeAt(i);
    hash |= 0;
  }
  const idx = Math.abs(hash + index) % FIELD_PALETTE.length;
  return FIELD_PALETTE[idx];
}

function getNormalizedStyle(bbox?: number[]) {
  if (!bbox || bbox.length < 4) return null;
  let [x1, y1, x2, y2] = bbox;
  // If absolute coordinates (> 1.5), scale based on standard A4 aspect
  if (x2 > 1.5 || y2 > 1.5) {
    x1 = Math.max(0, Math.min(1, x1 / 800));
    y1 = Math.max(0, Math.min(1, y1 / 1100));
    x2 = Math.max(0, Math.min(1, x2 / 800));
    y2 = Math.max(0, Math.min(1, y2 / 1100));
  }
  return {
    left: `${Math.max(0, Math.min(1, x1)) * 100}%`,
    top: `${Math.max(0, Math.min(1, y1)) * 100}%`,
    width: `${Math.max(0.005, Math.min(1, x2 - x1)) * 100}%`,
    height: `${Math.max(0.005, Math.min(1, y2 - y1)) * 100}%`,
  };
}

export function DocumentViewer({ document }: { document: DocumentRecord }) {
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<'fields' | 'rawText' | 'formattedText'>('fields');
  const [searchQuery, setSearchQuery] = useState('');

  // Debug Overlays State
  const [showFieldBoxes, setShowFieldBoxes] = useState(true);
  const [showOcrTokens, setShowOcrTokens] = useState(false);
  const [showLabels, setShowLabels] = useState(true);
  const [showConfidence, setShowConfidence] = useState(true);

  // Interaction State
  const [hoveredFieldId, setHoveredFieldId] = useState<string | null>(null);
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null);
  const [hoveredToken, setHoveredToken] = useState<OCRToken | null>(null);

  // Image load state
  const [imageError, setImageError] = useState(false);
  const [imageLoading, setImageLoading] = useState(true);
  const [copiedJson, setCopiedJson] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const totalPages = Math.max(1, document.pages || 1);

  const imageUrl = `/api/documents/preview/${encodeURIComponent(document.caseId)}/${encodeURIComponent(document.name)}?page=${page}&format=image`;

  const CANONICAL_TEMPLATE_FIELDS = [
    'applicant_name',
    'fathers_name',
    'dob',
    'mobile_no',
    'gender',
    'aadhaar_number',
    'pan_number',
    'address',
    'current_address',
    'bank_account_no',
    'type_of_account',
    'loan_amount',
    'loan_validity',
    'loan_type',
    'application_no',
    'application_date',
    'BPI',
    'irr_percent',
    'emi',
    'aadhaar_xml_present',
    'loan_agreement_present',
    'loan_agreement_signed',
  ];

  const getCanonicalJsonString = () => {
    if (document.formattedText && document.formattedText.trim().startsWith('{')) {
      try {
        const parsed = JSON.parse(document.formattedText);
        return JSON.stringify(parsed, null, 2);
      } catch {
        return document.formattedText;
      }
    }
    // Reconstruct canonical 22-field JSON from extracted fields
    const map: Record<string, any> = {};
    for (const f of document.extractedFields) {
      const normalizedKey = f.name.toLowerCase().replace(/\s+/g, '_');
      map[normalizedKey] = f.value;
    }
    const result: Record<string, any> = {};
    for (const key of CANONICAL_TEMPLATE_FIELDS) {
      if (key === 'aadhaar_xml_present' || key === 'loan_agreement_present' || key === 'loan_agreement_signed') {
        result[key] = Boolean(map[key]);
      } else {
        result[key] = map[key] ?? null;
      }
    }
    return JSON.stringify(result, null, 2);
  };

  useEffect(() => {
    setImageError(false);
    setImageLoading(true);
  }, [page, document.caseId, document.name]);

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleFieldSelect = (f: ExtractedField) => {
    setSelectedFieldId(f.id);
    if (f.page && f.page !== page) {
      setPage(f.page);
    }
    setExpandedIds((prev) => new Set(prev).add(f.id));

    // Scroll to bounding box if present
    setTimeout(() => {
      const boxEl = window.document.getElementById(`bbox-${f.id}`);
      if (boxEl) {
        boxEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }, 150);
  };

  const handleBoxClick = (f: ExtractedField, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedFieldId(f.id);
    setExpandedIds((prev) => new Set(prev).add(f.id));

    // Scroll to field in side panel
    const fieldEl = window.document.getElementById(`field-card-${f.id}`);
    if (fieldEl) {
      fieldEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  };

  const filteredFields = document.extractedFields.filter((f) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      f.name.toLowerCase().includes(q) ||
      String(f.value || '').toLowerCase().includes(q)
    );
  });

  // Current page fields with valid bounding boxes
  const pageFields = document.extractedFields.filter(
    (f) => (f.page || 1) === page && f.bbox && f.locationStatus !== 'unresolved'
  );

  // Current page OCR tokens
  const pageTokens: OCRToken[] =
    document.debug?.ocr_tokens_by_page?.[page] ||
    document.debug?.ocr_tokens?.filter((t) => t.page === page) ||
    [];

  return (
    <div className="card overflow-hidden flex flex-col h-full">
      {/* Primary Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-ink-200 bg-ink-50/50 flex-wrap">
        {/* Pagination */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="btn-ghost p-1.5 disabled:opacity-40"
            aria-label="Previous page"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="text-xs text-ink-600 tabular-nums px-1 font-mono">
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

        {/* Zoom Controls */}
        <button
          onClick={() => setZoom((z) => Math.max(0.4, Number((z - 0.1).toFixed(1))))}
          className="btn-ghost p-1.5"
          aria-label="Zoom out"
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
        >
          <ZoomIn className="h-4 w-4" />
        </button>
        <button
          onClick={() => setZoom(1)}
          className="btn-ghost p-1.5 text-xs text-ink-500 hover:text-ink-800"
          title="Reset Zoom"
        >
          <Maximize2 className="h-3.5 w-3.5" />
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
            Field BBoxes
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
            Raw OCR Text
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
            LLM Canonical JSON
          </button>
        </div>

        {/* Debug Overlay Toggles (only visible in fields view) */}
        {viewMode === 'fields' && (
          <div className="flex items-center gap-1.5 ml-2 bg-white/70 px-2 py-1 rounded-md border border-ink-200 text-xs">
            <span className="text-[11px] font-semibold text-ink-500 uppercase tracking-wider mr-1">
              Overlays:
            </span>

            <button
              onClick={() => setShowFieldBoxes(!showFieldBoxes)}
              className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors flex items-center gap-1 ${
                showFieldBoxes
                  ? 'bg-blue-100 text-blue-800 border border-blue-300'
                  : 'text-ink-500 hover:bg-ink-100'
              }`}
              title="Toggle Field Bounding Boxes"
            >
              <Box className="h-3 w-3" />
              Fields ({pageFields.length})
            </button>

            <button
              onClick={() => setShowOcrTokens(!showOcrTokens)}
              className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors flex items-center gap-1 ${
                showOcrTokens
                  ? 'bg-amber-100 text-amber-900 border border-amber-300'
                  : 'text-ink-500 hover:bg-ink-100'
              }`}
              title="Toggle Raw OCR Token Bounding Boxes"
            >
              <Crosshair className="h-3 w-3" />
              OCR Tokens ({pageTokens.length})
            </button>

            <button
              onClick={() => setShowLabels(!showLabels)}
              className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors flex items-center gap-1 ${
                showLabels
                  ? 'bg-ink-200 text-ink-800'
                  : 'text-ink-400 hover:bg-ink-100'
              }`}
              title="Toggle Field Labels on Bounding Boxes"
            >
              <Tag className="h-3 w-3" />
              Labels
            </button>

            <button
              onClick={() => setShowConfidence(!showConfidence)}
              className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors flex items-center gap-1 ${
                showConfidence
                  ? 'bg-emerald-100 text-emerald-800 border border-emerald-300'
                  : 'text-ink-400 hover:bg-ink-100'
              }`}
              title="Toggle Confidence Badges"
            >
              <CheckCircle2 className="h-3 w-3" />
              Conf %
            </button>
          </div>
        )}

        {/* Search */}
        <div className="ml-auto relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-ink-400" />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search fields…"
            className="input pl-8 py-1 text-xs w-44"
            aria-label="Search in document"
          />
        </div>
      </div>

      {/* Main Content Area */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_400px] flex-1 min-h-0">
        {/* Left: Document View / BBox Canvas */}
        <div className="relative bg-ink-100 flex items-start justify-center overflow-auto p-6 min-h-[500px]">
          {viewMode === 'rawText' ? (
            <div className="bg-white rounded-lg p-5 shadow-pop w-full h-full max-w-2xl overflow-y-auto font-mono text-xs text-ink-800 leading-relaxed whitespace-pre-wrap">
              <div className="flex items-center justify-between pb-3 mb-3 border-b border-ink-200 text-ink-500 font-sans">
                <span className="font-semibold text-ink-900 text-sm">
                  Raw OCR Text (Docling & RapidOCR PP-OCRv6)
                </span>
                <span>{document.pages} Pages</span>
              </div>
              {document.rawText || 'No raw OCR text available.'}
            </div>
          ) : viewMode === 'formattedText' ? (
            <div className="bg-white rounded-lg p-5 shadow-pop w-full h-full max-w-2xl overflow-y-auto flex flex-col">
              <div className="flex items-center justify-between pb-3 mb-3 border-b border-ink-200 text-ink-500 font-sans">
                <div className="flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-brand-600" />
                  <span className="font-semibold text-ink-900 text-sm">
                    Canonical Structured Output (LLM JSON)
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(getCanonicalJsonString());
                      setCopiedJson(true);
                      setTimeout(() => setCopiedJson(false), 2000);
                    }}
                    className="btn-secondary px-2.5 py-1 text-xs flex items-center gap-1.5 font-sans"
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
                  <span className="text-xs bg-brand-50 text-brand-700 px-2 py-0.5 rounded font-medium border border-brand-200">
                    22 Fields Template
                  </span>
                </div>
              </div>
              <pre className="font-mono text-xs text-ink-900 bg-ink-50/70 p-4 rounded-md border border-ink-200 leading-relaxed overflow-x-auto flex-1 select-all">
                {getCanonicalJsonString()}
              </pre>
            </div>
          ) : (
            /* Document Page Canvas with Percentage Bounding Boxes */
            <div
              ref={containerRef}
              className="relative inline-block transition-transform duration-100 origin-top shadow-xl rounded bg-white"
              style={{
                transform: `scale(${zoom})`,
                transformOrigin: 'top center',
              }}
            >
              {/* Document Page Image */}
              {!imageError ? (
                <div className="relative">
                  <img
                    src={imageUrl}
                    alt={`Document page ${page}`}
                    className="max-w-none block rounded select-none pointer-events-auto"
                    style={{ minWidth: '600px', width: '700px', height: 'auto' }}
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
                /* High-fidelity Fallback Canvas when image preview endpoint is unavailable */
                <div
                  className="bg-white border border-ink-300 rounded shadow-md relative"
                  style={{ width: '700px', height: '980px' }}
                >
                  <div className="absolute inset-0 bg-[radial-gradient(#e2e8f0_1px,transparent_1px)] [background-size:16px_16px] opacity-60" />
                  <div className="p-8 text-center text-ink-400 flex flex-col items-center justify-center h-full">
                    <FileText className="h-16 w-16 text-ink-300 mb-3" />
                    <p className="text-base font-semibold text-ink-800">{document.name}</p>
                    <p className="text-xs text-ink-500 mt-1">Page {page} of {totalPages}</p>
                    <div className="mt-3 px-3 py-1 bg-amber-50 border border-amber-200 text-amber-800 rounded text-xs">
                      Document image preview not rendered yet. Bounding boxes are active on the canvas below.
                    </div>
                  </div>
                </div>
              )}

              {/* OVERLAY LAYER 1: Raw OCR Tokens (When Enabled) */}
              {showOcrTokens && (
                <div className="absolute inset-0 pointer-events-none">
                  {pageTokens.map((tok) => {
                    const style = getNormalizedStyle(tok.normalized_bbox || tok.bbox);
                    if (!style) return null;
                    const isHovered = hoveredToken?.id === tok.id;
                    return (
                      <div
                        key={tok.id}
                        className={`absolute border border-dashed border-amber-500/70 bg-amber-500/10 pointer-events-auto cursor-crosshair transition-all ${
                          isHovered ? 'ring-2 ring-amber-600 bg-amber-500/30 z-30' : 'z-10'
                        }`}
                        style={style}
                        onMouseEnter={() => setHoveredToken(tok)}
                        onMouseLeave={() => setHoveredToken(null)}
                        title={`[Token] "${tok.text}" (Conf: ${Math.round(tok.confidence * 100)}%)`}
                      />
                    );
                  })}
                </div>
              )}

              {/* OVERLAY LAYER 2: Extracted Field Bounding Boxes */}
              {showFieldBoxes && (
                <div className="absolute inset-0 pointer-events-none">
                  {pageFields.map((f, idx) => {
                    const style = getNormalizedStyle(f.bbox);
                    if (!style) return null;
                    const color = getFieldColor(f.name, idx);
                    const isHovered = hoveredFieldId === f.id;
                    const isSelected = selectedFieldId === f.id;

                    return (
                      <div
                        id={`bbox-${f.id}`}
                        key={f.id}
                        onClick={(e) => handleBoxClick(f, e)}
                        onMouseEnter={() => setHoveredFieldId(f.id)}
                        onMouseLeave={() => setHoveredFieldId(null)}
                        className={`absolute border-2 ${color.border} ${color.bg} ${color.hoverBg} pointer-events-auto cursor-pointer transition-all duration-150 ${
                          isSelected
                            ? 'ring-4 ring-brand-500 ring-offset-1 z-40 shadow-lg scale-[1.01]'
                            : isHovered
                            ? 'ring-2 ring-brand-400 z-30 shadow-md'
                            : 'z-20'
                        }`}
                        style={style}
                      >
                        {/* Field Label / Confidence Pill */}
                        {(showLabels || isHovered || isSelected) && (
                          <div
                            className={`absolute -top-5 left-0 whitespace-nowrap text-[10px] font-bold px-1.5 py-0.5 rounded shadow-sm border flex items-center gap-1 pointer-events-none transition-transform ${
                              isSelected
                                ? 'bg-brand-600 text-white border-brand-700 -top-6 scale-105 z-50'
                                : `${color.badge} z-30`
                            }`}
                          >
                            <span>{f.name}</span>
                            {showConfidence && f.matchConfidence !== undefined && (
                              <span className="opacity-90 font-mono text-[9px]">
                                {Math.round(f.matchConfidence * 100)}%
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {/* OCR Token Hover Tooltip */}
              {hoveredToken && (
                <div className="absolute bottom-2 left-2 z-50 bg-ink-900/90 text-white text-[11px] px-3 py-1.5 rounded shadow-lg backdrop-blur-sm border border-ink-700 pointer-events-none font-mono">
                  <span className="text-amber-300 font-bold mr-1">OCR Token:</span>
                  <span className="text-white">"{hoveredToken.text}"</span>
                  <span className="text-ink-400 ml-2">Conf: {Math.round(hoveredToken.confidence * 100)}%</span>
                  {hoveredToken.line_id !== undefined && (
                    <span className="text-ink-400 ml-2">Line: {hoveredToken.line_id}</span>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right Side Panel: Extracted Fields with BBox Provenance & Debug Info */}
        <div className="border-t lg:border-t-0 lg:border-l border-ink-200 overflow-y-auto flex flex-col bg-white">
          <div className="px-4 py-3 border-b border-ink-100 flex items-center justify-between shrink-0 bg-ink-50/40">
            <div>
              <h3 className="text-sm font-semibold text-ink-800">
                Extracted Fields & Provenance
              </h3>
              <p className="text-xs text-ink-500 mt-0.5">
                {filteredFields.length} fields ({pageFields.length} on page {page})
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
              {filteredFields.map((f, idx) => {
                const isExpanded = expandedIds.has(f.id);
                const isSelected = selectedFieldId === f.id;
                const isHovered = hoveredFieldId === f.id;
                const isResolved = f.locationStatus === 'resolved' || (f.bbox && f.bbox.length === 4);
                const isUnresolved = f.locationStatus === 'unresolved' || !isResolved;
                const color = getFieldColor(f.name, idx);

                return (
                  <div
                    id={`field-card-${f.id}`}
                    key={f.id}
                    onMouseEnter={() => setHoveredFieldId(f.id)}
                    onMouseLeave={() => setHoveredFieldId(null)}
                    className={`transition-all ${
                      isSelected
                        ? 'bg-brand-50/60 border-l-4 border-l-brand-600'
                        : isHovered
                        ? 'bg-ink-50/70'
                        : 'hover:bg-ink-50/40'
                    }`}
                  >
                    {/* Header Row */}
                    <div className="w-full text-left px-4 py-2.5 flex items-start gap-2">
                      <button
                        onClick={() => toggleExpand(f.id)}
                        className="mt-1 text-ink-400 hover:text-ink-700 transition-colors"
                        aria-label="Toggle details"
                      >
                        {isExpanded ? (
                          <ChevronDown className="h-4 w-4 text-brand-600" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )}
                      </button>

                      <div
                        onClick={() => handleFieldSelect(f)}
                        className="flex-1 min-w-0 cursor-pointer"
                      >
                        <div className="flex items-center justify-between gap-1.5">
                          <span className="text-xs font-semibold text-ink-700 truncate flex items-center gap-1.5">
                            {isResolved && (
                              <span
                                className="w-2 h-2 rounded-full inline-block shrink-0"
                                style={{ backgroundColor: color.hex }}
                              />
                            )}
                            {f.name}
                          </span>

                          {/* Provenance Status Badges */}
                          <div className="flex items-center gap-1 shrink-0">
                            {isResolved ? (
                              <span
                                className="text-[10px] px-1.5 py-0.5 rounded font-medium flex items-center gap-1 bg-emerald-50 text-emerald-700 border border-emerald-200"
                                title={`Resolved via ${f.matchStrategy || 'OCR element match'}`}
                              >
                                <CheckCircle2 className="h-2.5 w-2.5 text-emerald-600" />
                                p.{f.page || 1}
                              </span>
                            ) : (
                              <span
                                className="text-[10px] px-1.5 py-0.5 rounded font-medium flex items-center gap-1 bg-amber-50 text-amber-700 border border-amber-200"
                                title={f.reason || 'Location unavailable: No matching OCR text'}
                              >
                                <AlertCircle className="h-2.5 w-2.5 text-amber-500" />
                                Location unavail.
                              </span>
                            )}

                            {f.source && (
                              <span
                                className={`text-[10px] px-1.5 py-0.5 rounded font-mono uppercase font-semibold ${
                                  f.source === 'vlm' || f.source === 'vlm_corrected'
                                    ? 'bg-review-50 text-review-700 border border-review-200'
                                    : f.source === 'docling' || f.source === 'docling_ocr' || f.source === 'DOCLING' || f.source === 'OCR' || f.source === 'ocr'
                                    ? 'bg-info-50 text-info-700 border border-info-200'
                                    : f.source === 'OPENROUTER_LLM' || f.source === 'llm'
                                    ? 'bg-purple-50 text-purple-700 border border-purple-200'
                                    : 'bg-ink-100 text-ink-600'
                                }`}
                              >
                                {f.source === 'OPENROUTER_LLM'
                                  ? 'LLM'
                                  : f.source === 'docling' || f.source === 'docling_ocr' || f.source === 'DOCLING'
                                  ? 'DOCLING_OCR'
                                  : f.source}
                              </span>
                            )}
                          </div>
                        </div>

                        <p className="text-sm font-semibold text-ink-900 mt-0.5 truncate font-mono">
                          {f.value === null ? (
                            <span className="text-ink-400 italic">null</span>
                          ) : (
                            String(f.value)
                          )}
                        </p>

                        <div className="mt-1 flex items-center gap-2">
                          <div className="flex-1 max-w-[120px]">
                            <ConfidenceBar value={f.confidence} size="sm" />
                          </div>
                          {f.matchConfidence !== undefined && (
                            <span className="text-[10px] text-ink-500 font-mono">
                              Match: {Math.round(f.matchConfidence * 100)}%
                            </span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Expanded Detail Panel (Debugging Inspector) */}
                    {isExpanded && (
                      <div className="px-4 pb-3 pt-1 ml-6 mr-3 border-l-2 border-brand-500 bg-brand-50/20 text-xs space-y-2 rounded-r-md">
                        {/* Raw Extracted Value */}
                        <div>
                          <span className="font-semibold text-ink-700">Extracted Value:</span>
                          <p className="font-mono text-ink-900 bg-white p-1.5 rounded border border-ink-200 mt-0.5 select-all">
                            {String(f.value || '')}
                          </p>
                        </div>

                        {/* Matched OCR Text Provenance */}
                        {f.matchedText && (
                          <div>
                            <span className="font-semibold text-ink-700">Matched OCR Text:</span>
                            <p className="font-mono text-xs text-ink-800 bg-emerald-50/60 p-1.5 rounded border border-emerald-200 mt-0.5">
                              "{f.matchedText}"
                            </p>
                          </div>
                        )}

                        {/* Unresolved Reason */}
                        {isUnresolved && (
                          <div className="p-2 bg-amber-50 border border-amber-200 rounded text-amber-800 text-[11px] flex items-start gap-1.5">
                            <AlertCircle className="h-3.5 w-3.5 text-amber-600 mt-0.5 shrink-0" />
                            <div>
                              <span className="font-semibold block">Location Unavailable</span>
                              <span>{f.reason || 'Value was not found in OCR text or tables.'}</span>
                            </div>
                          </div>
                        )}

                        {/* Bounding Box Coordinates */}
                        {f.bbox && (
                          <div className="space-y-1 bg-white p-2 rounded border border-ink-200 font-mono text-[11px]">
                            <div className="flex items-center justify-between text-ink-600">
                              <span className="font-semibold">Normalized BBox:</span>
                              <span>Page {f.page || 1}</span>
                            </div>
                            <div className="text-ink-900">
                              [{f.bbox.map((n) => n.toFixed(4)).join(', ')}]
                            </div>
                            {f.matchStrategy && (
                              <div className="text-[10px] text-brand-700 pt-0.5">
                                Strategy: <span className="font-semibold">{f.matchStrategy}</span>
                              </div>
                            )}
                          </div>
                        )}

                        {/* Multiple Candidates (If present) */}
                        {f.candidates && f.candidates.length > 1 && (
                          <div className="pt-1">
                            <span className="font-semibold text-ink-700 block mb-1 text-[11px]">
                              Alternative Matches ({f.candidates.length}):
                            </span>
                            <div className="space-y-1 max-h-24 overflow-y-auto">
                              {f.candidates.map((c, cIdx) => (
                                <div
                                  key={cIdx}
                                  className="text-[10px] bg-white p-1 rounded border border-ink-100 flex items-center justify-between"
                                >
                                  <span className="truncate max-w-[180px] font-mono">"{c.text}"</span>
                                  <span className="text-ink-500 font-mono">
                                    {Math.round(c.match_confidence * 100)}%
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Table Details */}
                        {f.type === 'table' && f.rows && (
                          <div className="mt-2 overflow-x-auto">
                            <span className="font-semibold text-ink-700 block mb-1">Table Grid:</span>
                            <table className="w-full text-[11px] border border-ink-200 bg-white rounded">
                              {f.headers && f.headers.length > 0 && (
                                <thead className="bg-ink-50 border-b border-ink-200">
                                  <tr>
                                    {f.headers.map((h, hIdx) => (
                                      <th key={hIdx} className="p-1 text-left font-semibold text-ink-700">
                                        {h}
                                      </th>
                                    ))}
                                  </tr>
                                </thead>
                              )}
                              <tbody>
                                {f.rows.map((row, rIdx) => (
                                  <tr key={rIdx} className="border-b border-ink-100">
                                    {row.map((cell, cIdx) => (
                                      <td key={cIdx} className="p-1 text-ink-800">
                                        {cell}
                                      </td>
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
