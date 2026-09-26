import { Fragment, useEffect, useMemo, useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import {
  Search,
  ArrowRight,
  Sparkles,
  UploadCloud,
  Folder,
  FolderOpen,
  ChevronDown,
  ChevronRight,
  Layers,
  List,
  ExternalLink,
  FileText,
  FlaskConical,
  X,
  ScanText,
  Trash2,
  Loader2,
} from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';
import { Pagination } from '@/components/ui/Pagination';
import { TableSkeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorState } from '@/components/ui/ErrorState';
import { UploadModal } from '@/components/documents/UploadModal';
import { documentsService } from '@/services';
import type { DocumentPage } from '@/services/documents';
import type { DocumentRecord } from '@/types';
import { useDebounced } from '@/hooks/useDebounced';

const PAGE_SIZE = 50; // Increased to show comprehensive grouped view per page

export default function DocumentsPage() {
  const [params, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState('');
  const debounced = useDebounced(query, 350);
  const [type, setType] = useState('ALL');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<DocumentPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [types, setTypes] = useState<string[]>([]);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [viewMode, setViewMode] = useState<'grouped' | 'flat'>('grouped');
  const [expandedCases, setExpandedCases] = useState<Record<string, boolean>>({});
  const [ocrOpen, setOcrOpen] = useState<Record<string, boolean>>({});
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const caseFilter = params.get('case') ?? undefined;

  useEffect(() => {
    documentsService.getTypes().then(setTypes).catch(() => {});
  }, []);

  const load = () => {
    setLoading(true);
    setError(false);
    documentsService
      .list({ query: debounced, type, caseId: caseFilter }, page, PAGE_SIZE)
      .then(setData)
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  };

  // Re-fetch without the loading skeleton, so open groups, open OCR previews and the scroll
  // position survive (used after a delete).
  const refreshInPlace = () =>
    documentsService
      .list({ query: debounced, type, caseId: caseFilter }, page, PAGE_SIZE)
      .then(setData)
      .catch(() => {});

  useEffect(load, [debounced, type, caseFilter, page]);

  // Auto-poll if any document is currently in PROCESSING state
  useEffect(() => {
    const hasProcessing = data?.items.some(
      (d) => d.ocrStatus === 'PROCESSING' || d.extractionStatus === 'PROCESSING'
    );
    if (hasProcessing) {
      const timer = setTimeout(() => {
        documentsService
          .list({ query: debounced, type, caseId: caseFilter }, page, PAGE_SIZE)
          .then(setData)
          .catch(() => {});
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [data, debounced, type, caseFilter, page]);

  const toggleOcr = (id: string) => setOcrOpen((prev) => ({ ...prev, [id]: !prev[id] }));

  const deleteDocument = async (d: DocumentRecord) => {
    const inCase = d.caseId && d.caseId !== 'GENERAL' && d.caseId !== 'SANDBOX' && d.caseId !== 'Unassigned';
    const confirmed = window.confirm(
      `Delete "${d.name}"?\n\nThis permanently removes the file and its OCR result.` +
        (inCase
          ? `\n\nIt is also removed from case ${d.caseId}. Existing verification results are not changed until the case is re-run.`
          : ''),
    );
    if (!confirmed) return;
    setDeletingId(d.id);
    try {
      const res = await documentsService.deleteDocument(d.id);
      if (res.errors?.length) {
        window.alert(`Deleted "${d.name}", but some files could not be removed:\n${res.errors.join('\n')}`);
      }
      setOcrOpen((prev) => {
        const next = { ...prev };
        delete next[d.id];
        return next;
      });
      await refreshInPlace();
    } catch (err) {
      console.error('Delete document failed:', err);
      window.alert(`Could not delete "${d.name}". It may already have been removed — refresh the list and try again.`);
    } finally {
      setDeletingId(null);
    }
  };

  const toggleCase = (cId: string) => {
    setExpandedCases((prev) => {
      const currentlyExpanded = prev[cId] ?? (caseFilter === cId);
      return { ...prev, [cId]: !currentlyExpanded };
    });
  };

  // Group items by caseId
  const { caseGroups, sandboxDocs } = useMemo(() => {
    const groups: Record<string, DocumentRecord[]> = {};
    const sandbox: DocumentRecord[] = [];

    (data?.items || []).forEach((doc) => {
      const cId = doc.caseId?.trim();
      if (!cId || cId === 'GENERAL' || cId === 'SANDBOX') {
        sandbox.push(doc);
      } else {
        if (!groups[cId]) groups[cId] = [];
        groups[cId].push(doc);
      }
    });

    return { caseGroups: groups, sandboxDocs: sandbox };
  }, [data?.items]);

  const renderDocumentTable = (docs: DocumentRecord[]) => (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead className="bg-ink-50/50">
          <tr>
            <th className="table-head">Document</th>
            <th className="table-head">Type</th>
            <th className="table-head">Pages</th>
            <th className="table-head">OCR</th>
            <th className="table-head">Extraction</th>
            <th className="table-head">Confidence</th>
            <th className="table-head">VLM</th>
            <th className="table-head">Uploaded (IST)</th>
            <th className="table-head text-right w-20">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {docs.map((d) => (
            <Fragment key={d.id}>
            <tr className="hover:bg-ink-50/50 transition-colors">
              <td className="table-cell font-medium text-ink-800">
                <Link
                  to={`/documents/${d.id}`}
                  className="inline-flex items-center gap-2 text-brand-600 hover:text-brand-700 hover:underline group"
                >
                  <FileText className="h-4 w-4 text-brand-500 shrink-0 group-hover:text-brand-600" />
                  <span className="font-medium">{d.name}</span>
                </Link>
              </td>
              <td className="table-cell">
                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-ink-100 text-ink-700">
                  {d.type}
                </span>
              </td>
              <td className="table-cell tabular-nums">{d.pages}</td>
              <td className="table-cell">
                <span
                  className={`chip ${
                    d.ocrStatus === 'COMPLETED'
                      ? 'bg-verified-50 text-verified-700'
                      : d.ocrStatus === 'PROCESSING'
                      ? 'bg-info-50 text-info-600 animate-pulse'
                      : 'bg-discrepancy-50 text-discrepancy-700'
                  }`}
                >
                  {d.ocrStatus.charAt(0) + d.ocrStatus.slice(1).toLowerCase()}
                </span>
              </td>
              <td className="table-cell">
                <span
                  className={`chip ${
                    d.extractionStatus === 'COMPLETED'
                      ? 'bg-verified-50 text-verified-700'
                      : d.extractionStatus === 'PROCESSING'
                      ? 'bg-info-50 text-info-600 animate-pulse'
                      : 'bg-discrepancy-50 text-discrepancy-700'
                  }`}
                >
                  {d.extractionStatus.charAt(0) + d.extractionStatus.slice(1).toLowerCase()}
                </span>
              </td>
              <td className="table-cell">
                <div className="w-28">
                  <ConfidenceBar value={d.confidence} size="sm" />
                </div>
              </td>
              <td className="table-cell">
                {d.vlmUsed ? (
                  <span className="chip bg-review-50 text-review-700">
                    <Sparkles className="h-3 w-3" /> Yes
                  </span>
                ) : (
                  <span className="text-ink-400 text-xs">No</span>
                )}
              </td>
              <td className="table-cell text-ink-500 font-mono text-xs whitespace-nowrap">
                {d.uploadedAt}
              </td>
              <td className="table-cell">
                <div className="flex items-center justify-end gap-1">
                  <button
                    type="button"
                    onClick={() => toggleOcr(d.id)}
                    aria-expanded={!!ocrOpen[d.id]}
                    aria-controls={`ocr-${d.id}`}
                    title={ocrOpen[d.id] ? 'Hide OCR text' : 'View OCR text'}
                    aria-label={`${ocrOpen[d.id] ? 'Hide' : 'View'} OCR text for ${d.name}`}
                    className={`p-1.5 rounded ${
                      ocrOpen[d.id] ? 'bg-brand-50 text-brand-700' : 'text-ink-500 hover:text-brand-700 hover:bg-brand-50'
                    }`}
                  >
                    <ScanText className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteDocument(d)}
                    disabled={deletingId === d.id}
                    title={`Delete ${d.name}`}
                    aria-label={`Delete ${d.name}`}
                    className="p-1.5 rounded text-ink-400 hover:text-discrepancy-600 hover:bg-discrepancy-50 disabled:opacity-50"
                  >
                    {deletingId === d.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                  </button>
                </div>
              </td>
            </tr>
            {ocrOpen[d.id] && (
              <tr id={`ocr-${d.id}`} className="bg-ink-50/40">
                <td colSpan={9} className="px-4 pb-4 pt-1">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold text-ink-700">
                      Raw OCR text · {d.pages} page{d.pages === 1 ? '' : 's'}
                      {d.ocrEngine?.label ? ` · ${d.ocrEngine.label}` : ''}
                    </span>
                    <Link to={`/documents/${d.id}`} className="text-xs text-brand-600 hover:text-brand-700 inline-flex items-center gap-1">
                      Open full viewer <ArrowRight className="h-3 w-3" />
                    </Link>
                  </div>
                  {d.ocrStatus === 'COMPLETED' && d.rawText?.trim() ? (
                    <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border border-ink-200 bg-white p-3 font-mono text-xs leading-relaxed text-ink-800">
                      {d.rawText}
                    </pre>
                  ) : (
                    <p className="text-xs text-ink-500">
                      {d.ocrStatus === 'PROCESSING'
                        ? 'OCR is still running — the text appears here when it finishes.'
                        : d.ocrStatus === 'PENDING'
                        ? 'Not processed yet. Documents uploaded into a case are read when the case runs OCR or verification.'
                        : 'No OCR text was produced for this document.'}
                    </p>
                  )}
                </td>
              </tr>
            )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );

  return (
    <div className="space-y-4">
      <PageHeader
        title="Documents"
        subtitle={
          caseFilter ? (
            <span className="inline-flex items-center gap-2">
              <span>Showing documents for case</span>
              <span className="font-semibold text-ink-800 bg-ink-100 px-2 py-0.5 rounded text-xs">
                {caseFilter}
              </span>
              <button
                onClick={() => {
                  params.delete('case');
                  setSearchParams(params);
                }}
                className="inline-flex items-center gap-1 text-xs text-brand-600 hover:text-brand-700 underline"
              >
                <X className="h-3 w-3" /> View all cases
              </button>
            </span>
          ) : (
            'Document repository categorized by loan case and ad-hoc IDP sandbox.'
          )
        }
        actions={
          <button onClick={() => setUploadOpen(true)} className="btn-primary">
            <UploadCloud className="h-4 w-4" /> Upload Documents
          </button>
        }
      />

      <div className="card p-4">
        <div className="flex flex-col md:flex-row gap-3 items-center justify-between">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 w-full md:w-auto md:flex-1">
            <div className="relative md:col-span-2">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-ink-400" />
              <input
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setPage(1);
                }}
                placeholder="Search document name or case ID…"
                className="input pl-9"
                aria-label="Search documents"
              />
            </div>
            <select
              value={type}
              onChange={(e) => {
                setType(e.target.value);
                setPage(1);
              }}
              className="select"
              aria-label="Type filter"
            >
              <option value="ALL">All types</option>
              {types.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>

          <div className="inline-flex items-center rounded-lg bg-ink-100 p-1 shrink-0 self-end md:self-center">
            <button
              onClick={() => setViewMode('grouped')}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                viewMode === 'grouped'
                  ? 'bg-white text-ink-900 shadow-sm'
                  : 'text-ink-600 hover:text-ink-900'
              }`}
            >
              <Layers className="h-3.5 w-3.5" /> By Loan Case
            </button>
            <button
              onClick={() => setViewMode('flat')}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                viewMode === 'flat'
                  ? 'bg-white text-ink-900 shadow-sm'
                  : 'text-ink-600 hover:text-ink-900'
              }`}
            >
              <List className="h-3.5 w-3.5" /> Flat List
            </button>
          </div>
        </div>
      </div>

      {loading ? (
        <TableSkeleton rows={6} cols={8} />
      ) : error ? (
        <ErrorState title="Unable to load documents" onRetry={load} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title="No documents found"
          description="Try changing your filters or upload documents to get started."
        />
      ) : viewMode === 'flat' ? (
        <>
          <div className="card overflow-hidden">{renderDocumentTable(data.items)}</div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPageChange={setPage} />
        </>
      ) : (
        <div className="space-y-4">
          {/* Ad-Hoc Sandbox Section (if any standalone documents exist) */}
          {sandboxDocs.length > 0 && (
            <div className="card border-l-4 border-l-purple-500 overflow-hidden shadow-sm">
              <div
                onClick={() => toggleCase('__SANDBOX__')}
                className="p-4 bg-purple-50/40 hover:bg-purple-50/70 transition-colors flex items-center justify-between cursor-pointer border-b border-ink-100"
              >
                <div className="flex items-center gap-3">
                  <span className="p-1.5 bg-purple-100 text-purple-700 rounded-md">
                    <FlaskConical className="h-4 w-4" />
                  </span>
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-bold text-ink-900">Ad-Hoc OCR Sandbox</h3>
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-purple-100 text-purple-800">
                        {sandboxDocs.length} {sandboxDocs.length === 1 ? 'doc' : 'docs'}
                      </span>
                    </div>
                    <p className="text-xs text-ink-500 mt-0.5">
                      Standalone test documents processed via IDP / OCR without binding to a loan case.
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button className="text-ink-400 hover:text-ink-600 p-1">
                    {expandedCases['__SANDBOX__'] ? (
                      <ChevronDown className="h-5 w-5" />
                    ) : (
                      <ChevronRight className="h-5 w-5" />
                    )}
                  </button>
                </div>
              </div>
              {expandedCases['__SANDBOX__'] && renderDocumentTable(sandboxDocs)}
            </div>
          )}

          {/* Grouped Loan Cases */}
          {Object.entries(caseGroups).map(([cId, docs]) => {
            const isExpanded = !!expandedCases[cId] || (!!caseFilter && caseFilter === cId);
            return (
              <div key={cId} className="card overflow-hidden shadow-sm">
                <div
                  onClick={() => toggleCase(cId)}
                  className="p-4 bg-ink-50/40 hover:bg-ink-50/80 transition-colors flex items-center justify-between cursor-pointer border-b border-ink-100"
                >
                  <div className="flex items-center gap-3">
                    <span className="p-1.5 bg-brand-50 text-brand-700 rounded-md">
                      {isExpanded ? (
                        <FolderOpen className="h-4 w-4 text-brand-600" />
                      ) : (
                        <Folder className="h-4 w-4" />
                      )}
                    </span>
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="text-sm font-bold text-ink-900 font-mono">{cId}</h3>
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-ink-100 text-ink-700">
                          {docs.length} {docs.length === 1 ? 'document' : 'documents'}
                        </span>
                      </div>
                      <p className="text-xs text-ink-500 mt-0.5">
                        Loan Application Document Package
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-3">
                    <Link
                      to={`/cases/${cId}`}
                      onClick={(e) => e.stopPropagation()}
                      className="inline-flex items-center gap-1.5 text-xs font-medium text-brand-600 hover:text-brand-700 bg-white hover:bg-brand-50 px-2.5 py-1.5 rounded-md border border-ink-200 transition-colors shadow-2xs"
                    >
                      <span>View Case Details</span>
                      <ExternalLink className="h-3.5 w-3.5" />
                    </Link>
                    <button className="text-ink-400 hover:text-ink-600 p-1">
                      {isExpanded ? (
                        <ChevronDown className="h-5 w-5" />
                      ) : (
                        <ChevronRight className="h-5 w-5" />
                      )}
                    </button>
                  </div>
                </div>

                {isExpanded && renderDocumentTable(docs)}
              </div>
            );
          })}

          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPageChange={setPage} />
        </div>
      )}

      <UploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        caseId={caseFilter}
        onUploaded={load}
      />
    </div>
  );
}
