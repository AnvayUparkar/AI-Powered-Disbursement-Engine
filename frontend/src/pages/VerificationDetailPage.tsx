import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';
import { Skeleton, CardSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/ui/ErrorState';
import { DGCLScorecard } from '@/components/verification/DGCLScorecard';
import { CheckpointDrawer } from '@/components/verification/CheckpointDrawer';
import { ProcessingPipeline } from '@/components/documents/ProcessingPipeline';
import { casesService } from '@/services';
import type { Case, Checkpoint } from '@/types';

export default function VerificationDetailPage() {
  const { caseId } = useParams();
  const [c, setC] = useState<Case | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [drawer, setDrawer] = useState<Checkpoint | null>(null);

  const load = () => {
    if (!caseId) return;
    setLoading(true);
    setError(false);
    casesService.getById(caseId).then(setC).catch(() => setError(true)).finally(() => setLoading(false));
  };

  useEffect(load, [caseId]);

  if (loading) {
    return (
      <div>
        <Skeleton className="h-4 w-32 mb-4" />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2"><CardSkeleton /></div>
          <CardSkeleton />
        </div>
      </div>
    );
  }

  if (error || !c) {
    return (
      <div>
        <Link to="/verification" className="inline-flex items-center gap-1.5 text-sm text-brand-600 hover:text-brand-700 mb-4">
          <ArrowLeft className="h-4 w-4" /> Back to Verification
        </Link>
        <ErrorState title="Unable to load verification" onRetry={load} />
      </div>
    );
  }

  return (
    <div>
      <Link to="/verification" className="inline-flex items-center gap-1.5 text-sm text-brand-600 hover:text-brand-700 mb-4">
        <ArrowLeft className="h-4 w-4" /> Back to Verification
      </Link>

      <div className="card p-5 mb-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold text-ink-900">{c.id}</h1>
              <StatusBadge status={c.status} size="md" />
            </div>
            <p className="text-sm text-ink-500 mt-1">{c.applicant} · {c.loanType}</p>
          </div>
          <div className="text-right">
            <p className="text-xs text-ink-500">DGCL Confidence</p>
            <p className="text-xl font-semibold text-ink-900 tabular-nums">{c.dgclScore.toFixed(1)}%</p>
            <div className="mt-1 w-40 ml-auto"><ConfidenceBar value={c.dgclScore} threshold={90} /></div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2">
          <h2 className="text-sm font-semibold text-ink-800 mb-3">DGCL Scorecard</h2>
          <DGCLScorecard checkpoints={c.checkpoints} onCheckpointClick={setDrawer} />
        </div>
        <div>
          <ProcessingPipeline steps={c.processingSteps} />
        </div>
      </div>

      <CheckpointDrawer checkpoint={drawer} onClose={() => setDrawer(null)} />
    </div>
  );
}
