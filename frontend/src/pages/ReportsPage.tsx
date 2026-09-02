import { useEffect, useState } from 'react';
import { PageHeader } from '@/components/ui/PageHeader';
import { KpiCard } from '@/components/ui/KpiCard';
import { CardSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/ui/ErrorState';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';
import { BarChart, LineChart, DualBarChart } from '@/components/ui/Charts';
import { reportsService } from '@/services';
import type { ReportSummary } from '@/types';
import { FolderKanban, CheckCircle2, XCircle, AlertTriangle, Clock, Sparkles } from 'lucide-react';

const fmtTime = (s: number) => `${Math.floor(s / 60)}m ${s % 60}s`;

export default function ReportsPage() {
  const [data, setData] = useState<ReportSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = () => {
    setLoading(true);
    setError(false);
    reportsService.getReportSummary().then(setData).catch(() => setError(true)).finally(() => setLoading(false));
  };

  useEffect(load, []);

  if (loading) {
    return (
      <div>
        <PageHeader title="Reports" subtitle="Verification performance and processing analytics." />
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4 mb-6">
          {Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <CardSkeleton /><CardSkeleton /><CardSkeleton /><CardSkeleton />
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div>
        <PageHeader title="Reports" />
        <ErrorState title="Unable to load reports" onRetry={load} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="Reports" subtitle="Verification performance and processing analytics." />

      {/* Summary KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4 mb-6">
        <KpiCard label="Total Cases" value={data.totalCases.toLocaleString('en-IN')} icon={FolderKanban} tone="info" />
        <KpiCard label="Verified" value={data.verified.toLocaleString('en-IN')} icon={CheckCircle2} tone="verified" />
        <KpiCard label="Discrepancies" value={data.discrepancies.toLocaleString('en-IN')} icon={XCircle} tone="discrepancy" />
        <KpiCard label="Indeterminate" value={data.indeterminate.toLocaleString('en-IN')} icon={AlertTriangle} tone="review" />
        <KpiCard label="Avg Processing" value={fmtTime(data.avgProcessingSeconds)} icon={Clock} tone="neutral" />
        <KpiCard label="VLM Fallback" value={`${data.vlmFallbackPct.toFixed(1)}%`} icon={Sparkles} tone="review" />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-800 mb-4">Discrepancy Trends</h3>
          <BarChart data={data.discrepancyTrend.map((d) => ({ label: d.day, value: d.count }))} />
        </div>
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-800 mb-4">Review Workload (Created vs Resolved)</h3>
          <DualBarChart data={data.reviewWorkload.map((d) => ({ label: d.day, a: d.created, b: d.resolved }))} />
          <div className="flex gap-4 mt-3 text-xs text-ink-500">
            <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-brand-500" /> Created</span>
            <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-verified-500" /> Resolved</span>
          </div>
        </div>
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-800 mb-4">Processing Latency</h3>
          <LineChart data={data.processingLatency.map((d) => ({ label: d.day, value: d.seconds }))} unit="s" />
        </div>
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-800 mb-4">VLM Fallback Rate</h3>
          <LineChart data={data.vlmFallbackTrend.map((d) => ({ label: d.day, value: d.pct }))} unit="%" />
        </div>
      </div>

      {/* Checkpoint performance */}
      <div className="card p-5 mb-5">
        <h3 className="text-sm font-semibold text-ink-800 mb-4">DGCL Checkpoint Performance</h3>
        <div className="space-y-2.5">
          {data.checkpointPerformance.map((cp) => (
            <div key={cp.id} className="flex items-center gap-3">
              <span className="font-mono text-xs text-ink-400 w-6">{String(cp.id).padStart(2, '0')}</span>
              <span className="text-sm text-ink-700 w-44 shrink-0">{cp.name}</span>
              <div className="flex-1"><ConfidenceBar value={cp.passRate} showLabel={false} size="sm" /></div>
              <span className="font-mono text-xs text-ink-600 tabular-nums w-14 text-right">{cp.passRate.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>

      {/* Extraction accuracy */}
      <div className="card p-5">
        <h3 className="text-sm font-semibold text-ink-800 mb-4">Document Extraction Accuracy</h3>
        <LineChart data={data.extractionAccuracy.map((d) => ({ label: d.day, value: d.pct }))} unit="%" />
      </div>
    </div>
  );
}
