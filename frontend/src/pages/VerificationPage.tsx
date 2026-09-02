import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ShieldCheck, ArrowRight } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';
import { CardSkeleton, TableSkeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorState } from '@/components/ui/ErrorState';
import { casesService } from '@/services';
import type { Case } from '@/types';

export default function VerificationPage() {
  const [cases, setCases] = useState<Case[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = () => {
    setLoading(true);
    setError(false);
    casesService.list({}, null, 1, 50).then((r) => setCases(r.items)).catch(() => setError(true)).finally(() => setLoading(false));
  };

  useEffect(load, []);

  return (
    <div>
      <PageHeader title="Verification" subtitle="DGCL verification results across all cases." />
      {loading ? (
        <TableSkeleton rows={5} cols={6} />
      ) : error ? (
        <ErrorState title="Unable to load verification results" onRetry={load} />
      ) : cases.length === 0 ? (
        <EmptyState title="No verification results" />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {cases.map((c) => {
            const verified = c.checkpoints.filter((cp) => cp.status === 'VERIFIED').length;
            const discrep = c.checkpoints.filter((cp) => cp.status === 'DISCREPANCY').length;
            const indet = c.checkpoints.filter((cp) => cp.status === 'INDETERMINATE').length;
            return (
              <Link key={c.id} to={`/verification/${c.id}`} className="card card-hover p-4 block">
                <div className="flex items-center justify-between gap-2 mb-2">
                  <span className="font-mono text-sm font-medium text-brand-700">{c.id}</span>
                  <StatusBadge status={c.status} />
                </div>
                <p className="text-sm text-ink-700">{c.applicant} · {c.loanType}</p>
                <div className="mt-3">
                  <div className="flex justify-between text-xs text-ink-500 mb-1">
                    <span>DGCL Score</span>
                    <span className="font-mono tabular-nums">{c.dgclScore.toFixed(1)}%</span>
                  </div>
                  <ConfidenceBar value={c.dgclScore} threshold={90} size="sm" />
                </div>
                <div className="flex gap-3 mt-3 text-xs">
                  <span className="text-verified-700">{verified} verified</span>
                  <span className="text-discrepancy-700">{discrep} discrepancies</span>
                  <span className="text-review-700">{indet} review</span>
                </div>
                <div className="mt-3 pt-3 border-t border-ink-100 flex items-center justify-between">
                  <span className="inline-flex items-center gap-1 text-xs text-ink-500"><ShieldCheck className="h-3.5 w-3.5" /> View scorecard</span>
                  <ArrowRight className="h-3.5 w-3.5 text-ink-400" />
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
