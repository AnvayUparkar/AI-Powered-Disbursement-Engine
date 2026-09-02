import { useEffect, useRef, useState } from 'react';
import { useParams, useSearchParams, Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  FileText,
  Clock,
  ShieldCheck,
  ClipboardList,
  UploadCloud,
  Play,
  Loader2,
  CheckCircle2,
  Sparkles,
} from 'lucide-react';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';
import { CardSkeleton, Skeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/ui/ErrorState';
import { DGCLScorecard } from '@/components/verification/DGCLScorecard';
import { CheckpointDrawer } from '@/components/verification/CheckpointDrawer';
import { ProcessingPipeline } from '@/components/documents/ProcessingPipeline';
import { UploadModal } from '@/components/documents/UploadModal';
import { AnimatedPipelineStepper } from '@/components/pipeline/AnimatedPipelineStepper';
import { casesService, reviewService } from '@/services';
import type { Case, Checkpoint, ReviewItem, PipelineEvent, PipelineStage } from '@/types';

const inr = (n: number) => '₹' + n.toLocaleString('en-IN');

export default function CaseDetailPage() {
  const { caseId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [c, setC] = useState<Case | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [pipelineVisible, setPipelineVisible] = useState(false);
  const [currentStage, setCurrentStage] = useState<PipelineStage>('fetch');
  const [completedStages, setCompletedStages] = useState<string[]>([]);
  const [subnodeRollups, setSubnodeRollups] = useState<Record<string, string>>({});
  const [pipelineErrors, setPipelineErrors] = useState<string[]>([]);
  const [error, setError] = useState(false);
  const [drawer, setDrawer] = useState<Checkpoint | null>(null);
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [uploadOpen, setUploadOpen] = useState(false);
  const autoRunHandled = useRef(false);

  const load = () => {
    if (!caseId) return;
    setLoading(true);
    setError(false);
    casesService
      .getById(caseId)
      .then((res) => {
        setC(res);
        if (res) reviewService.getByCaseId(res.id).then(setReviews).catch(() => {});
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  };

  const startPipelineStream = () => {
    if (!caseId) return;
    setPipelineVisible(true);
    setRunning(true);
    setCurrentStage('fetch');
    setCompletedStages([]);
    setPipelineErrors([]);

    const closeStream = casesService.streamPipeline(
      caseId,
      (evt: PipelineEvent) => {
        if (evt.stage === 'start') {
          setCurrentStage('fetch');
        } else if (evt.stage === 'finish') {
          setCurrentStage('finish');
          setCompletedStages(['fetch', 'extract', 'comparison', 'compile', 'checker', 'scorecard', 'push']);
          setRunning(false);
          load();
        } else if (evt.stage === 'error') {
          setPipelineErrors((prev) => [...prev, evt.message || 'Pipeline execution error']);
          setRunning(false);
        } else {
          setCurrentStage(evt.stage);
          setCompletedStages((prev) => (prev.includes(evt.stage) ? prev : [...prev, evt.stage]));
          if (evt.subnode_rollups) {
            setSubnodeRollups(evt.subnode_rollups);
          }
          if (evt.errors && evt.errors.length > 0) {
            setPipelineErrors(evt.errors);
          }
        }
      },
      (err) => {
        console.warn('SSE stream error/fallback, triggering standard execution:', err);
        casesService
          .runVerification(caseId)
          .then((res) => {
            if (res && res.case) setC(res.case);
          })
          .catch(console.error)
          .finally(() => {
            setRunning(false);
            load();
          });
      },
    );

    return closeStream;
  };

  useEffect(load, [caseId]);

  // Handle autoRun query parameter (when redirected from CreateCaseModal)
  useEffect(() => {
    if (searchParams.get('autoRun') === 'true' && !autoRunHandled.current && caseId) {
      autoRunHandled.current = true;
      startPipelineStream();
    }
  }, [searchParams, caseId]);

  if (error && !pipelineVisible && !running && !c) {
    return (
      <div>
        <Link
          to="/cases"
          className="inline-flex items-center gap-1.5 text-sm text-brand-600 hover:text-brand-700 mb-4"
        >
          <ArrowLeft className="h-4 w-4" /> Back to Cases
        </Link>
        <ErrorState
          title="Unable to load case"
          description="This case may not exist or could not be fetched."
          onRetry={load}
        />
      </div>
    );
  }

  if (!c) {
    return (
      <div>
        <Link
          to="/cases"
          className="inline-flex items-center gap-1.5 text-sm text-brand-600 hover:text-brand-700 mb-4"
        >
          <ArrowLeft className="h-4 w-4" /> Back to Cases
        </Link>

        {/* Animated Live Pipeline Stepper Banner */}
        <div className="mb-5">
          <AnimatedPipelineStepper
            currentStage={currentStage}
            completedStages={completedStages}
            subnodeRollups={subnodeRollups}
            isRunning={running || true}
            errors={pipelineErrors}
            title={`Live Verification Pipeline: Case ${caseId}`}
          />
        </div>

        <div className="card p-6 text-center">
          <Loader2 className="h-6 w-6 animate-spin text-brand-600 mx-auto mb-2" />
          <p className="text-sm font-medium text-ink-800">Initializing case verification...</p>
          <p className="text-xs text-ink-500 mt-0.5">LangGraph agent is processing documents and running verification checkpoints.</p>
        </div>
      </div>
    );
  }

  const verified = c.checkpoints.filter((cp) => cp.status === 'VERIFIED').length;
  const discrepancies = c.checkpoints.filter((cp) => cp.status === 'DISCREPANCY').length;
  const indeterminate = c.checkpoints.filter((cp) => cp.status === 'INDETERMINATE').length;

  return (
    <div>
      <Link
        to="/cases"
        className="inline-flex items-center gap-1.5 text-sm text-brand-600 hover:text-brand-700 mb-4"
      >
        <ArrowLeft className="h-4 w-4" /> Back to Cases
      </Link>

      {/* Animated Live Pipeline Stepper Banner */}
      {(pipelineVisible || running) && (
        <div className="mb-5">
          <AnimatedPipelineStepper
            currentStage={currentStage}
            completedStages={completedStages}
            subnodeRollups={subnodeRollups}
            isRunning={running}
            errors={pipelineErrors}
            title={`Live Verification Pipeline: Case ${c.id}`}
          />
        </div>
      )}

      {/* Header */}
      <div className="card p-5 mb-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-2xl font-semibold text-ink-900 tracking-tight">{c.id}</h1>
              <StatusBadge status={c.status} size="md" />
              <button
                onClick={startPipelineStream}
                disabled={running}
                className="btn btn-primary inline-flex items-center gap-2 text-xs font-semibold py-1.5 px-3 rounded-lg shadow-sm hover:shadow transition-all"
              >
                {running ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Running LangGraph Engine...
                  </>
                ) : (
                  <>
                    <Play className="h-3.5 w-3.5 fill-current" /> Run Verification Engine
                  </>
                )}
              </button>
            </div>
            <p className="text-sm text-ink-500 mt-1">
              {c.loanType} · {c.applicant}
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs text-ink-500 uppercase tracking-wide">DGCL Confidence</p>
            <p className="text-2xl font-semibold text-ink-900 tabular-nums">
              {c.dgclScore.toFixed(1)}%
            </p>
            <div className="mt-1 w-40 ml-auto">
              <ConfidenceBar value={c.dgclScore} threshold={90} />
            </div>
          </div>
        </div>

        {/* Summary grid */}
        <dl className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-x-6 gap-y-3 mt-5 pt-5 border-t border-ink-100">
          <div>
            <dt className="text-xs text-ink-500">Applicant</dt>
            <dd className="text-sm font-medium text-ink-800 mt-0.5">{c.applicant}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-500">Application ID</dt>
            <dd className="text-sm font-medium text-ink-800 mt-0.5">{c.applicationId}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-500">Loan Amount</dt>
            <dd className="text-sm font-medium text-ink-800 mt-0.5 tabular-nums">
              {inr(c.loanAmount)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-ink-500">Disbursal Amount</dt>
            <dd className="text-sm font-medium text-ink-800 mt-0.5 tabular-nums">
              {c.disbursalDate ? inr(c.disbursalAmount) : '—'}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-ink-500">Loan Type</dt>
            <dd className="text-sm font-medium text-ink-800 mt-0.5">{c.loanType}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-500">Login Date</dt>
            <dd className="text-sm font-medium text-ink-800 mt-0.5">{c.loginDate}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-500">Disbursal Date</dt>
            <dd className="text-sm font-medium text-ink-800 mt-0.5">
              {c.disbursalDate ?? 'Pending'}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-ink-500">Documents</dt>
            <dd className="text-sm font-medium text-ink-800 mt-0.5 tabular-nums">
              {c.documentCount}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-ink-500">Processing Time</dt>
            <dd className="text-sm font-medium text-ink-800 mt-0.5 inline-flex items-center gap-1">
              <Clock className="h-3.5 w-3.5 text-ink-400" />
              {c.processingTime}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-ink-500">Last Updated</dt>
            <dd className="text-sm font-medium text-ink-800 mt-0.5">{c.lastUpdated}</dd>
          </div>
        </dl>

        {/* Quick stats */}
        <div className="flex flex-wrap gap-4 mt-5 pt-5 border-t border-ink-100 text-sm">
          <span className="inline-flex items-center gap-1.5 text-verified-700">
            <ShieldCheck className="h-4 w-4" /> {verified} Verified
          </span>
          <span className="inline-flex items-center gap-1.5 text-discrepancy-700">
            <ShieldCheck className="h-4 w-4" /> {discrepancies} Discrepancies
          </span>
          <span className="inline-flex items-center gap-1.5 text-review-700">
            <ClipboardList className="h-4 w-4" /> {indeterminate} Needs Review
          </span>
          <Link
            to={`/documents?case=${c.id}`}
            className="ml-auto inline-flex items-center gap-1.5 text-brand-600 hover:text-brand-700 text-xs font-medium"
          >
            <FileText className="h-3.5 w-3.5" /> View documents
          </Link>
          <button
            onClick={() => setUploadOpen(true)}
            className="inline-flex items-center gap-1.5 text-brand-600 hover:text-brand-700 text-xs font-medium"
          >
            <UploadCloud className="h-3.5 w-3.5" /> Upload
          </button>
          <Link
            to={`/verification/${c.id}`}
            className="inline-flex items-center gap-1.5 text-brand-600 hover:text-brand-700 text-xs font-medium"
          >
            <ShieldCheck className="h-3.5 w-3.5" /> Verification detail
          </Link>
        </div>
      </div>

      {/* Main layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2">
          <h2 className="text-sm font-semibold text-ink-800 mb-3">DGCL Scorecard</h2>
          <DGCLScorecard checkpoints={c.checkpoints} onCheckpointClick={setDrawer} />
        </div>
        <div className="space-y-5">
          <ProcessingPipeline steps={c.processingSteps} />

          {reviews.length > 0 && (
            <div className="card p-4">
              <h3 className="text-sm font-semibold text-ink-800 mb-3">Review Items</h3>
              <div className="space-y-2">
                {reviews.map((r) => (
                  <div key={r.id} className="flex items-start gap-2 text-sm">
                    <span
                      className={`chip ${
                        r.priority === 'HIGH'
                          ? 'bg-discrepancy-50 text-discrepancy-700'
                          : 'bg-review-50 text-review-700'
                      }`}
                    >
                      {r.priority}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-ink-700 truncate">{r.issue}</p>
                      <p className="text-xs text-ink-500">{r.checkpointName}</p>
                    </div>
                    <button
                      onClick={() => navigate(`/review?case=${r.caseId}`)}
                      className="text-xs text-brand-600 hover:text-brand-700 font-medium shrink-0"
                    >
                      Review →
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <CheckpointDrawer checkpoint={drawer} onClose={() => setDrawer(null)} />
      <UploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        caseId={c.id}
        onUploaded={load}
      />
    </div>
  );
}

