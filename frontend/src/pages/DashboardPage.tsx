import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  FolderKanban,
  FileText,
  CheckCircle2,
  XCircle,
  ClipboardList,
  TrendingUp,
  Clock,
  ScanLine,
  Sparkles,
  ArrowRight,
} from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { KpiCard } from '@/components/ui/KpiCard';
import { CardSkeleton, TableSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/ui/ErrorState';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';
import { reportsService, casesService } from '@/services';
import type { DashboardKpis, Case } from '@/types';
import { checkpointPerformance } from '@/mock';

export default function DashboardPage() {
  const [kpis, setKpis] = useState<DashboardKpis | null>(null);
  const [recent, setRecent] = useState<Case[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = () => {
    setLoading(true);
    setError(false);
    Promise.all([reportsService.getDashboardKpis(), casesService.getRecent(6)])
      .then(([k, r]) => {
        setKpis(k);
        setRecent(r);
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  if (loading) {
    return (
      <div>
        <PageHeader title="Disbursal Verification Dashboard" subtitle="Monitor document processing, DGCL validation and review workload." />
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4 mb-6">
          {Array.from({ length: 5 }).map((_, i) => <CardSkeleton key={i} />)}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
          <CardSkeleton />
          <CardSkeleton />
        </div>
        <TableSkeleton rows={5} cols={7} />
      </div>
    );
  }

  if (error || !kpis) {
    return (
      <div>
        <PageHeader title="Disbursal Verification Dashboard" />
        <ErrorState title="Unable to load dashboard data" onRetry={load} />
      </div>
    );
  }

  const fmtTime = (s: number) => `${Math.floor(s / 60)}m ${s % 60}s`;

  return (
    <div>
      <PageHeader
        title="Disbursal Verification Dashboard"
        subtitle="Monitor document processing, DGCL validation and review workload."
      />

      {/* KPI cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4 mb-6">
        <KpiCard label="Cases Today" value={kpis.casesProcessedToday.toLocaleString('en-IN')} icon={FolderKanban} tone="info" />
        <KpiCard label="Documents Processed" value={kpis.documentsProcessed.toLocaleString('en-IN')} icon={FileText} tone="neutral" />
        <KpiCard label="Verified" value={kpis.verified.toLocaleString('en-IN')} icon={CheckCircle2} tone="verified" />
        <KpiCard label="Discrepancies" value={kpis.discrepancies.toLocaleString('en-IN')} icon={XCircle} tone="discrepancy" />
        <KpiCard label="Needs Review" value={kpis.needsReview.toLocaleString('en-IN')} icon={ClipboardList} tone="review" />
      </div>

      {/* Accuracy & Performance */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
        <div className="card p-5">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp className="h-4.5 w-4.5 text-verified-600" />
            <h3 className="text-sm font-semibold text-ink-800">Verification Accuracy</h3>
          </div>
          <p className="text-3xl font-semibold text-ink-900 tabular-nums">{kpis.dgclValidation.toFixed(1)}%</p>
          <p className="text-xs text-ink-500 mt-1">Overall DGCL Validation</p>
          <div className="mt-3">
            <ConfidenceBar value={kpis.dgclValidation} threshold={kpis.dgclTarget} />
          </div>
          <p className="text-xs text-ink-500 mt-2">Target ≥ {kpis.dgclTarget}%</p>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2 mb-3">
            <Clock className="h-4.5 w-4.5 text-brand-600" />
            <h3 className="text-sm font-semibold text-ink-800">Processing Performance</h3>
          </div>
          <p className="text-3xl font-semibold text-ink-900 tabular-nums">{fmtTime(kpis.avgProcessingSeconds)}</p>
          <p className="text-xs text-ink-500 mt-1">Average Case Processing</p>
          <div className="mt-3">
            <ConfidenceBar value={(kpis.avgProcessingSeconds / kpis.avgProcessingTargetSeconds) * 100} />
          </div>
          <p className="text-xs text-ink-500 mt-2">Target ≤ {fmtTime(kpis.avgProcessingTargetSeconds)}</p>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2 mb-3">
            <ScanLine className="h-4.5 w-4.5 text-info-600" />
            <h3 className="text-sm font-semibold text-ink-800">Document Processing</h3>
          </div>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between"><dt className="text-ink-500">Processed today</dt><dd className="font-medium text-ink-800 tabular-nums">{kpis.docProcessedToday.toLocaleString('en-IN')}</dd></div>
            <div className="flex justify-between"><dt className="text-ink-500">OCR success rate</dt><dd className="font-medium text-verified-700 tabular-nums">{kpis.ocrSuccessRate.toFixed(1)}%</dd></div>
            <div className="flex justify-between"><dt className="text-ink-500">VLM fallback rate</dt><dd className="font-medium text-review-700 tabular-nums">{kpis.vlmFallbackRate.toFixed(1)}%</dd></div>
            <div className="flex justify-between"><dt className="text-ink-500">Extraction success</dt><dd className="font-medium text-ink-800 tabular-nums">{kpis.extractionSuccessRate.toFixed(1)}%</dd></div>
            <div className="flex justify-between"><dt className="text-ink-500">Avg processing</dt><dd className="font-medium text-ink-800 tabular-nums">{fmtTime(kpis.avgDocProcessingSeconds)}</dd></div>
          </dl>
        </div>
      </div>

      {/* Checkpoint performance */}
      <div className="card p-5 mb-6">
        <div className="flex items-center gap-2 mb-4">
          <Sparkles className="h-4.5 w-4.5 text-brand-600" />
          <h3 className="text-sm font-semibold text-ink-800">DGCL Checkpoint Performance</h3>
        </div>
        <div className="space-y-2.5">
          {checkpointPerformance.map((cp) => (
            <div key={cp.id} className="flex items-center gap-3">
              <span className="font-mono text-xs text-ink-400 w-6">{String(cp.id).padStart(2, '0')}</span>
              <span className="text-sm text-ink-700 w-40 shrink-0">{cp.name}</span>
              <div className="flex-1">
                <ConfidenceBar value={cp.passRate} showLabel={false} size="sm" />
              </div>
              <span className="font-mono text-xs text-ink-600 tabular-nums w-14 text-right">{cp.passRate.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>

      {/* Recent cases */}
      <div className="card overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-ink-200">
          <h3 className="text-sm font-semibold text-ink-800">Recent Cases</h3>
          <Link to="/cases" className="text-xs font-medium text-brand-600 hover:text-brand-700 inline-flex items-center gap-1">
            View all <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-ink-50/50">
              <tr>
                <th className="table-head">Case ID</th>
                <th className="table-head">Applicant</th>
                <th className="table-head">Loan Type</th>
                <th className="table-head">Docs</th>
                <th className="table-head">DGCL Score</th>
                <th className="table-head">Status</th>
                <th className="table-head">Time</th>
                <th className="table-head">Updated</th>
                <th className="table-head"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {recent.map((c) => (
                <tr key={c.id} className="hover:bg-ink-50/50">
                  <td className="table-cell font-medium text-brand-700">{c.id}</td>
                  <td className="table-cell">{c.applicant}</td>
                  <td className="table-cell">{c.loanType}</td>
                  <td className="table-cell tabular-nums">{c.documentCount}</td>
                  <td className="table-cell"><span className="font-mono tabular-nums">{c.dgclScore.toFixed(1)}%</span></td>
                  <td className="table-cell"><StatusBadge status={c.status} /></td>
                  <td className="table-cell tabular-nums">{c.processingTime}</td>
                  <td className="table-cell text-ink-500">{c.lastUpdated.split(' ')[1]}</td>
                  <td className="table-cell">
                    <Link to={`/cases/${c.id}`} className="text-brand-600 hover:text-brand-700 text-xs font-medium inline-flex items-center gap-1">
                      Open <ArrowRight className="h-3 w-3" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
