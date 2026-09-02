import { useEffect, useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { Search, ArrowRight, Sparkles, UploadCloud } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';
import { Pagination } from '@/components/ui/Pagination';
import { TableSkeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorState } from '@/components/ui/ErrorState';
import { UploadModal } from '@/components/documents/UploadModal';
import { documentsService } from '@/services';
import type { DocumentPage } from '@/services/documents';
import { useDebounced } from '@/hooks/useDebounced';

const PAGE_SIZE = 10;

export default function DocumentsPage() {
  const [params] = useSearchParams();
  const [query, setQuery] = useState('');
  const debounced = useDebounced(query, 350);
  const [type, setType] = useState('ALL');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<DocumentPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [types, setTypes] = useState<string[]>([]);
  const [uploadOpen, setUploadOpen] = useState(false);

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

  useEffect(load, [debounced, type, caseFilter, page]);

  return (
    <div>
      <PageHeader
        title="Documents"
        subtitle={caseFilter ? `Documents for case ${caseFilter}` : 'Document repository across all cases.'}
        actions={
          <button onClick={() => setUploadOpen(true)} className="btn-primary">
            <UploadCloud className="h-4 w-4" /> Upload Documents
          </button>
        }
      />

      <div className="card p-4 mb-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="relative md:col-span-2">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-ink-400" />
            <input
              value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(1); }}
              placeholder="Search document name or case ID…"
              className="input pl-9"
              aria-label="Search documents"
            />
          </div>
          <select value={type} onChange={(e) => { setType(e.target.value); setPage(1); }} className="select" aria-label="Type filter">
            <option value="ALL">All types</option>
            {types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </div>

      {loading ? (
        <TableSkeleton rows={6} cols={8} />
      ) : error ? (
        <ErrorState title="Unable to load documents" onRetry={load} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState title="No documents found" description="Try changing your filters or search terms." />
      ) : (
        <>
          <div className="card overflow-hidden">
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
                    <th className="table-head">Uploaded</th>
                    <th className="table-head"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {data.items.map((d) => (
                    <tr key={d.id} className="hover:bg-ink-50/50">
                      <td className="table-cell font-medium text-ink-800">{d.name}</td>
                      <td className="table-cell">{d.type}</td>
                      <td className="table-cell tabular-nums">{d.pages}</td>
                      <td className="table-cell">
                        <span className={`chip ${d.ocrStatus === 'COMPLETED' ? 'bg-verified-50 text-verified-700' : d.ocrStatus === 'PROCESSING' ? 'bg-info-50 text-info-600' : 'bg-discrepancy-50 text-discrepancy-700'}`}>
                          {d.ocrStatus.charAt(0) + d.ocrStatus.slice(1).toLowerCase()}
                        </span>
                      </td>
                      <td className="table-cell">
                        <span className={`chip ${d.extractionStatus === 'COMPLETED' ? 'bg-verified-50 text-verified-700' : d.extractionStatus === 'PROCESSING' ? 'bg-info-50 text-info-600' : 'bg-discrepancy-50 text-discrepancy-700'}`}>
                          {d.extractionStatus.charAt(0) + d.extractionStatus.slice(1).toLowerCase()}
                        </span>
                      </td>
                      <td className="table-cell">
                        <div className="w-28"><ConfidenceBar value={d.confidence} size="sm" /></div>
                      </td>
                      <td className="table-cell">
                        {d.vlmUsed ? (
                          <span className="chip bg-review-50 text-review-700"><Sparkles className="h-3 w-3" /> Yes</span>
                        ) : (
                          <span className="text-ink-400 text-xs">No</span>
                        )}
                      </td>
                      <td className="table-cell text-ink-500">{d.uploadedAt}</td>
                      <td className="table-cell">
                        <Link to={`/documents/${d.id}`} className="text-brand-600 hover:text-brand-700 text-xs font-medium inline-flex items-center gap-1">
                          View <ArrowRight className="h-3 w-3" />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPageChange={setPage} />
        </>
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
