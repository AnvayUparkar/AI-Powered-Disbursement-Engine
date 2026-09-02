import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, XCircle, AlertTriangle, Info, History } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { TableSkeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorState } from '@/components/ui/ErrorState';
import { auditService } from '@/services';
import type { AuditEvent } from '@/types';

const resultIcon = (r: AuditEvent['result']) => {
  switch (r) {
    case 'SUCCESS': return <CheckCircle2 className="h-4 w-4 text-verified-600" />;
    case 'FAILED': return <XCircle className="h-4 w-4 text-discrepancy-600" />;
    case 'WARNING': return <AlertTriangle className="h-4 w-4 text-review-600" />;
    case 'INFO': return <Info className="h-4 w-4 text-info-600" />;
  }
};

export default function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = () => {
    setLoading(true);
    setError(false);
    auditService.list().then(setEvents).catch(() => setError(true)).finally(() => setLoading(false));
  };

  useEffect(load, []);

  return (
    <div>
      <PageHeader title="Audit Log" subtitle="Complete timeline of system and operator actions." />

      {loading ? (
        <TableSkeleton rows={8} cols={4} />
      ) : error ? (
        <ErrorState title="Unable to load audit log" onRetry={load} />
      ) : events.length === 0 ? (
        <EmptyState title="No audit events" icon={History} />
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-ink-50/50">
                <tr>
                  <th className="table-head">Timestamp</th>
                  <th className="table-head">Result</th>
                  <th className="table-head">Action</th>
                  <th className="table-head">Component</th>
                  <th className="table-head">Confidence</th>
                  <th className="table-head">Case</th>
                  <th className="table-head">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {events.map((e) => (
                  <tr key={e.id} className="hover:bg-ink-50/50">
                    <td className="table-cell font-mono text-ink-600 tabular-nums">{e.timestamp}</td>
                    <td className="table-cell">{resultIcon(e.result)}</td>
                    <td className="table-cell text-ink-800">{e.action}</td>
                    <td className="table-cell"><span className="chip bg-ink-100 text-ink-600">{e.component}</span></td>
                    <td className="table-cell">{e.confidence !== undefined ? <span className="font-mono tabular-nums text-ink-600">{e.confidence.toFixed(1)}%</span> : <span className="text-ink-400">—</span>}</td>
                    <td className="table-cell">{e.caseId ? <Link to={`/cases/${e.caseId}`} className="text-brand-600 hover:text-brand-700 font-medium">{e.caseId}</Link> : <span className="text-ink-400">—</span>}</td>
                    <td className="table-cell text-ink-500 max-w-xs truncate">{e.detail ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
