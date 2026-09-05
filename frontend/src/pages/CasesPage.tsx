import { useEffect, useMemo, useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { Search, Plus } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { Pagination } from '@/components/ui/Pagination';
import { SortHeader } from '@/components/ui/SortHeader';
import { TableSkeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorState } from '@/components/ui/ErrorState';
import { CreateCaseModal } from '@/components/cases/CreateCaseModal';
import { casesService } from '@/services';
import type { CasePage, SortState } from '@/services/cases';
import type { CaseStatus, RiskLevel } from '@/types';
import { useDebounced } from '@/hooks/useDebounced';

const PAGE_SIZE = 8;
const statuses: (CaseStatus | 'ALL')[] = ['ALL', 'VERIFIED', 'DISCREPANCY', 'INDETERMINATE', 'PROCESSING'];
const risks: (RiskLevel | 'ALL')[] = ['ALL', 'LOW', 'MEDIUM', 'HIGH'];

export default function CasesPage() {
  const [params] = useSearchParams();
  const [query, setQuery] = useState(params.get('q') ?? '');
  const debounced = useDebounced(query, 350);
  const [status, setStatus] = useState<CaseStatus | 'ALL'>('ALL');
  const [risk, setRisk] = useState<RiskLevel | 'ALL'>('ALL');
  const [loanType, setLoanType] = useState('ALL');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [sort, setSort] = useState<SortState | null>({ key: 'lastUpdated', dir: 'desc' });
  const [page, setPage] = useState(1);
  const [data, setData] = useState<CasePage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [loanTypes, setLoanTypes] = useState<string[]>([]);
  const [createModalOpen, setCreateModalOpen] = useState(false);

  useEffect(() => {
    casesService.getLoanTypes().then(setLoanTypes).catch(() => {});
  }, []);

  const load = () => {
    setLoading(true);
    setError(false);
    casesService
      .list({ query: debounced, status, risk, loanType, dateFrom, dateTo }, sort, page, PAGE_SIZE)
      .then(setData)
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  };

  useEffect(load, [debounced, status, risk, loanType, dateFrom, dateTo, sort, page]);

  const toggleSort = (key: SortState['key']) => {
    setSort((p) =>
      p && p.key === key ? { key, dir: p.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' },
    );
  };

  const resetFilters = () => {
    setQuery('');
    setStatus('ALL');
    setRisk('ALL');
    setLoanType('ALL');
    setDateFrom('');
    setDateTo('');
    setPage(1);
  };

  const hasFilters = useMemo(
    () => debounced || status !== 'ALL' || risk !== 'ALL' || loanType !== 'ALL' || dateFrom || dateTo,
    [debounced, status, risk, loanType, dateFrom, dateTo],
  );

  return (
    <div>
      <PageHeader
        title="Loan Cases"
        subtitle="Search and manage loan disbursal verification cases."
        actions={
          <button
            onClick={() => setCreateModalOpen(true)}
            className="btn-primary inline-flex items-center gap-1.5 shadow-sm"
          >
            <Plus className="h-4 w-4" /> Create Case
          </button>
        }
      />

      {/* Filters */}
      <div className="card p-4 mb-4">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="relative md:col-span-2">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-ink-400" />
            <input
              value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(1); }}
              placeholder="Search Case ID, applicant, or application ID…"
              className="input pl-9"
              aria-label="Search cases"
            />
          </div>
          <select value={status} onChange={(e) => { setStatus(e.target.value as CaseStatus | 'ALL'); setPage(1); }} className="select" aria-label="Status filter">
            {statuses.map((s) => <option key={s} value={s}>{s === 'ALL' ? 'All statuses' : s}</option>)}
          </select>
          <select value={risk} onChange={(e) => { setRisk(e.target.value as RiskLevel | 'ALL'); setPage(1); }} className="select" aria-label="Risk filter">
            {risks.map((r) => <option key={r} value={r}>{r === 'ALL' ? 'All risk levels' : r}</option>)}
          </select>
          <select value={loanType} onChange={(e) => { setLoanType(e.target.value); setPage(1); }} className="select" aria-label="Loan type filter">
            <option value="ALL">All loan types</option>
            {loanTypes.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <div className="flex items-center gap-2">
            <input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(1); }} className="input" aria-label="Date from" />
            <span className="text-ink-400 text-xs">to</span>
            <input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(1); }} className="input" aria-label="Date to" />
          </div>
          {hasFilters && (
            <button onClick={resetFilters} className="btn-secondary justify-self-start">Clear filters</button>
          )}
        </div>
      </div>

      {loading ? (
        <TableSkeleton rows={6} cols={10} />
      ) : error ? (
        <ErrorState title="Unable to load cases" onRetry={load} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState title="No cases found" description="Try changing your filters or search terms." />
      ) : (
        <>
          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-ink-50/50">
                  <tr>
                    <th className="table-head"><SortHeader label="Case ID" active={sort?.key === 'id'} dir={sort?.dir ?? 'asc'} onClick={() => toggleSort('id')} /></th>
                    <th className="table-head"><SortHeader label="Applicant" active={sort?.key === 'applicant'} dir={sort?.dir ?? 'asc'} onClick={() => toggleSort('applicant')} /></th>
                    <th className="table-head">Loan Type</th>
                    <th className="table-head">Docs</th>
                    <th className="table-head"><SortHeader label="DGCL Score" active={sort?.key === 'dgclScore'} dir={sort?.dir ?? 'asc'} onClick={() => toggleSort('dgclScore')} /></th>
                    <th className="table-head">V</th>
                    <th className="table-head">D</th>
                    <th className="table-head">R</th>
                    <th className="table-head"><SortHeader label="Time" active={sort?.key === 'processingTimeSeconds'} dir={sort?.dir ?? 'asc'} onClick={() => toggleSort('processingTimeSeconds')} /></th>
                    <th className="table-head"><SortHeader label="Status" active={sort?.key === 'status'} dir={sort?.dir ?? 'asc'} onClick={() => toggleSort('status')} /></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {data.items.map((c) => (
                    <tr key={c.id} className="hover:bg-ink-50/50">
                      <td className="table-cell font-medium">
                        <Link to={`/cases/${c.id}`} className="text-brand-600 hover:text-brand-800 hover:underline">
                          {c.id}
                        </Link>
                      </td>
                      <td className="table-cell">{c.applicant}</td>
                      <td className="table-cell">{c.loanType}</td>
                      <td className="table-cell tabular-nums">{c.documentCount}</td>
                      <td className="table-cell"><span className="font-mono tabular-nums">{c.dgclScore.toFixed(1)}%</span></td>
                      <td className="table-cell text-verified-700 tabular-nums">{c.verifiedCount}</td>
                      <td className="table-cell text-discrepancy-700 tabular-nums">{c.discrepancyCount}</td>
                      <td className="table-cell text-review-700 tabular-nums">{c.reviewCount}</td>
                      <td className="table-cell tabular-nums">{c.processingTime}</td>
                      <td className="table-cell"><StatusBadge status={c.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPageChange={setPage} />
        </>
      )}

      <CreateCaseModal
        open={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        onCaseCreated={() => load()}
      />
    </div>
  );
}

