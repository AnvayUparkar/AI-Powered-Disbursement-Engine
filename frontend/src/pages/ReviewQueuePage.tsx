import { useEffect, useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { Search, ArrowRight, ClipboardList } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Pagination } from '@/components/ui/Pagination';
import { TableSkeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorState } from '@/components/ui/ErrorState';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';
import { reviewService } from '@/services';
import type { ReviewPage } from '@/services/review';
import { useDebounced } from '@/hooks/useDebounced';

const PAGE_SIZE = 10;

export default function ReviewQueuePage() {
  const [params] = useSearchParams();
  const [query, setQuery] = useState('');
  const debounced = useDebounced(query, 350);
  const [priority, setPriority] = useState('ALL');
  const [assigned, setAssigned] = useState<'ALL' | 'UNASSIGNED' | 'ASSIGNED'>('ALL');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<ReviewPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const caseFilter = params.get('case') ?? undefined;

  const load = () => {
    setLoading(true);
    setError(false);
    reviewService
      .list({ query: debounced, priority, assigned }, page, PAGE_SIZE)
      .then(setData)
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  };

  useEffect(load, [debounced, priority, assigned, page]);

  return (
    <div>
      <PageHeader title="Review Queue" subtitle="Cases requiring human verification and intervention." />

      <div className="card p-4 mb-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="relative md:col-span-2">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-ink-400" />
            <input
              value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(1); }}
              placeholder="Search case ID, issue, or checkpoint…"
              className="input pl-9"
              aria-label="Search review items"
            />
          </div>
          <div className="flex gap-2">
            <select value={priority} onChange={(e) => { setPriority(e.target.value); setPage(1); }} className="select" aria-label="Priority filter">
              <option value="ALL">All priorities</option>
              <option value="HIGH">High</option>
              <option value="MEDIUM">Medium</option>
              <option value="LOW">Low</option>
            </select>
            <select value={assigned} onChange={(e) => { setAssigned(e.target.value as 'ALL' | 'UNASSIGNED' | 'ASSIGNED'); setPage(1); }} className="select" aria-label="Assigned filter">
              <option value="ALL">All</option>
              <option value="UNASSIGNED">Unassigned</option>
              <option value="ASSIGNED">Assigned</option>
            </select>
          </div>
        </div>
      </div>

      {loading ? (
        <TableSkeleton rows={5} cols={7} />
      ) : error ? (
        <ErrorState title="Unable to load review queue" onRetry={load} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState title="No items in review queue" description="All cases have been processed or reviewed." icon={ClipboardList} />
      ) : (
        <>
          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-ink-50/50">
                  <tr>
                    <th className="table-head">Case ID</th>
                    <th className="table-head">Issue</th>
                    <th className="table-head">Checkpoint</th>
                    <th className="table-head">Confidence</th>
                    <th className="table-head">Priority</th>
                    <th className="table-head">Created</th>
                    <th className="table-head">Assigned</th>
                    <th className="table-head"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {data.items.map((r) => (
                    <tr key={r.id} className="hover:bg-ink-50/50">
                      <td className="table-cell font-medium text-brand-700">{r.caseId}</td>
                      <td className="table-cell">{r.issue}</td>
                      <td className="table-cell">{r.checkpointName}</td>
                      <td className="table-cell">
                        {r.confidence > 0 ? <div className="w-24"><ConfidenceBar value={r.confidence} size="sm" /></div> : <span className="text-ink-400 text-xs">—</span>}
                      </td>
                      <td className="table-cell">
                        <span className={`chip ${r.priority === 'HIGH' ? 'bg-discrepancy-50 text-discrepancy-700' : r.priority === 'MEDIUM' ? 'bg-review-50 text-review-700' : 'bg-ink-100 text-ink-600'}`}>
                          {r.priority}
                        </span>
                      </td>
                      <td className="table-cell text-ink-500">{r.createdAt}</td>
                      <td className="table-cell">{r.assignedTo ?? <span className="text-ink-400 text-xs">Unassigned</span>}</td>
                      <td className="table-cell">
                        <Link to={`/review/${r.id}`} className="text-brand-600 hover:text-brand-700 text-xs font-medium inline-flex items-center gap-1">
                          Review <ArrowRight className="h-3 w-3" />
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
    </div>
  );
}
