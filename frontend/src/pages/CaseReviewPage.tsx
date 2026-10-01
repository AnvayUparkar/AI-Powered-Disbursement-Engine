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
  Printer,
  Trash2,
  AlertTriangle,
  ScanLine,
  ChevronDown,
  ArrowRight,
  MoreHorizontal,
} from 'lucide-react';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { ErrorState } from '@/components/ui/ErrorState';
import { CheckpointDrawer } from '@/components/verification/CheckpointDrawer';
import { PrintScorecardModal } from '@/components/verification/PrintScorecardModal';
import { UploadModal } from '@/components/documents/UploadModal';
import { casesService } from '@/services';
import type { Case, Checkpoint, PipelineEvent, PipelineStage } from '@/types';

const inr = (n: number) => '₹' + n.toLocaleString('en-IN');

const formatSourceName = (src?: string) => {
  if (!src) return '—';
  const s = src.toLowerCase();
  if (s === 'los') return 'LOS';
  if (s === 'pan') return 'PAN';
  if (s === 'aadhaar') return 'Aadhaar';
  if (s === 'aadhaar_xml') return 'Aadhaar XML';
  if (s === 'application_form') return 'Application Form';
  if (s === 'loan_agreement') return 'Loan Agreement';
  if (s === 'kfs') return 'KFS';
  if (s === 'sanction_letter' || s === 'sanction') return 'Sanction Letter';
  if (s === 'account_statement') return 'Account Statement';
  if (s === 'bpi') return 'BPI';
  if (s === 'disbursal_memo' || s === 'memo') return 'Disbursal Memo';
  if (s === 'bt_details' || s === 'bt') return 'BT Details';
  return src.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
};

function CaseReviewScorecard({
  checkpoints,
  onCheckpointClick,
}: {
  checkpoints: Checkpoint[];
  onCheckpointClick?: (cp: Checkpoint) => void;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);

  const toggle = (id: number) => setExpanded((p) => (p === id ? null : id));

  return (
    <div className="card divide-y divide-ink-100">
      {checkpoints.map((cp) => {
        const isOpen = expanded === cp.id;
        const na = cp.status === 'NOT_APPLICABLE';
        return (
          <div key={cp.id} className={na ? 'opacity-60' : ''}>
            <div className="flex items-start gap-3 px-4 py-3.5">
              <span className="font-mono text-xs text-ink-400 mt-0.5 w-6 shrink-0">
                {String(cp.id).padStart(2, '0')}
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <button
                    onClick={() => onCheckpointClick?.(cp)}
                    className="text-sm font-medium text-ink-800 hover:text-brand-600 text-left"
                  >
                    {cp.name}
                  </button>
                  <StatusBadge status={cp.status} />
                  {cp.totalFields != null && cp.totalFields > 0 ? (
                    <span className="text-xs text-ink-500 ml-1">
                      · matched {cp.matchedFields ?? 0} / {cp.totalFields} fields
                    </span>
                  ) : null}
                </div>
                {isOpen && (
                  <div className="mt-3 space-y-3 animate-fade-in">
                    {cp.comparisons && cp.comparisons.length > 0 ? (
                      <div className="overflow-x-auto rounded border border-ink-100">
                        <table className="w-full text-xs text-left">
                          <thead>
                            <tr className="bg-ink-50/80 text-[10px] uppercase tracking-wider text-ink-400 border-b border-ink-100">
                              <th className="py-1.5 px-2 font-medium w-12 text-ink-400">Sr No</th>
                              <th className="py-1.5 px-2 font-medium">Field</th>
                              <th className="py-1.5 px-2 font-medium">Source Document</th>
                              <th className="py-1.5 px-2 font-medium">Doc Value</th>
                              <th className="py-1.5 px-2 font-medium">LOS Value</th>
                              <th className="py-1.5 px-2 font-medium text-right">Result</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-ink-100">
                            {cp.comparisons.map((c, idx) => {
                              const fieldName = c.field
                                ? c.field.replace(/_/g, ' ').replace(/^\w/, (chr) => chr.toUpperCase())
                                : 'Check';
                              const sourceDoc = formatSourceName(c.sources?.[0]);
                              const docVal =
                                c.values?.[0] !== null && c.values?.[0] !== undefined ? String(c.values[0]) : '—';
                              const losVal =
                                c.values?.[1] !== null && c.values?.[1] !== undefined ? String(c.values[1]) : '—';
                              const result = c.match_status || 'INDETERMINATE';

                              return (
                                <tr key={c.check_id || idx} className={idx % 2 === 1 ? 'bg-ink-50/40' : ''}>
                                  <td className="py-1.5 px-2 font-mono text-ink-400 text-xs">
                                    {String(idx + 1).padStart(2, '0')}
                                  </td>
                                  <td className="py-1.5 px-2 font-medium text-ink-800">
                                    {fieldName}
                                  </td>
                                  <td className="py-1.5 px-2 text-ink-600">
                                    {sourceDoc}
                                  </td>
                                  <td className="py-1.5 px-2 text-ink-700 font-mono">
                                    {docVal}
                                  </td>
                                  <td className="py-1.5 px-2 text-ink-700 font-mono">
                                    {losVal}
                                  </td>
                                  <td className="py-1.5 px-2 text-right">
                                    <span
                                      className={`chip text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded ring-1 ring-inset ${
                                        result === 'MATCH'
                                          ? 'bg-verified-50 text-verified-700 ring-verified-500/20'
                                          : result === 'MISMATCH'
                                            ? 'bg-discrepancy-50 text-discrepancy-700 ring-discrepancy-500/20'
                                            : 'bg-review-50 text-review-700 ring-review-500/20'
                                      }`}
                                    >
                                      {result}
                                    </span>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-xs text-ink-500 italic">No comparison data available.</p>
                    )}
                    <div className="flex flex-wrap items-center justify-end text-xs text-ink-500">
                      <button
                        onClick={() => onCheckpointClick?.(cp)}
                        className="inline-flex items-center gap-1 text-brand-600 hover:text-brand-700 font-medium"
                      >
                        View details <ArrowRight className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                )}
              </div>
              <button
                onClick={() => toggle(cp.id)}
                className="shrink-0 p-1 text-ink-400 hover:text-ink-600"
                aria-label={isOpen ? 'Collapse' : 'Expand'}
              >
                <ChevronDown
                  className={`h-4 w-4 transition-transform ${isOpen ? 'rotate-180' : ''}`}
                />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function CaseReviewPage() {
  const { caseId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [c, setC] = useState<Case | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runningOcr, setRunningOcr] = useState(false);
  const [pipelineVisible, setPipelineVisible] = useState(false);
  const [currentStage, setCurrentStage] = useState<PipelineStage>('fetch');
  const [completedStages, setCompletedStages] = useState<string[]>([]);
  const [subnodeRollups, setSubnodeRollups] = useState<Record<string, string>>({});
  const [pipelineErrors, setPipelineErrors] = useState<string[]>([]);
  const [error, setError] = useState(false);
  const [drawer, setDrawer] = useState<Checkpoint | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [printModalOpen, setPrintModalOpen] = useState(false);
  const [showIssuesOnly, setShowIssuesOnly] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const autoRunHandled = useRef(false);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleDeleteCase = async () => {
    if (!caseId) return;
    const confirmed = window.confirm(
      `Delete case ${caseId} and ALL its documents? This permanently removes the LOS record, every uploaded document, and all extraction/verification results. This cannot be undone.`,
    );
    if (!confirmed) return;

    try {
      setDeleting(true);
      await casesService.deleteCase(caseId);
      navigate('/cases');
    } catch (err) {
      console.error('Failed to delete case:', err);
      window.alert('Failed to delete case. Check the console/backend logs for details.');
      setDeleting(false);
    }
  };

  const handleRunOcr = async () => {
    if (!caseId) return;
    try {
      setRunningOcr(true);
      const res = await casesService.runOcr(caseId);
      if (res && res.case) {
        setC(res.case);
      }
      load();
    } catch (err) {
      console.error('Failed to run OCR engine:', err);
      window.alert('Failed to run OCR engine. Check backend logs for details.');
    } finally {
      setRunningOcr(false);
    }
  };

  const load = () => {
    if (!caseId) return;
    setLoading(true);
    setError(false);
    casesService
      .getById(caseId)
      .then((res) => {
        setC(res);
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

      {/* Missing LOS Banner */}
      {c.hasLosData === false && (
        <div className="mb-5 rounded-xl border border-amber-200 bg-amber-50/90 p-4 text-amber-900 shadow-sm flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 text-amber-600 mt-0.5 flex-shrink-0" />
          <div className="flex-1">
            <h4 className="text-sm font-semibold text-amber-900">LOS Record Not Found</h4>
            <p className="text-xs text-amber-800 mt-0.5 leading-relaxed">
              No Loan Origination System (LOS) record was found for case <strong className="font-semibold">{c.id}</strong>. Checkpoints comparing documents against LOS will remain indeterminate until an LOS record is provided.
            </p>
          </div>
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
                onClick={() => setPrintModalOpen(true)}
                className="btn btn-secondary inline-flex items-center gap-2 text-xs font-semibold py-1.5 px-3 rounded-lg shadow-sm hover:shadow transition-all"
                title="Print official HDB verification scorecard with 12 checkpoints and comparison audit"
              >
                <Printer className="h-3.5 w-3.5" /> Print Scorecard
              </button>

              <div className="relative inline-block" ref={menuRef}>
                <button
                  onClick={() => setMenuOpen((prev) => !prev)}
                  className="btn btn-secondary p-1.5 rounded-lg shadow-sm hover:shadow transition-all inline-flex items-center justify-center"
                  aria-label="More actions"
                  aria-expanded={menuOpen}
                  title="More actions"
                >
                  <MoreHorizontal className="h-4 w-4 text-ink-700" />
                </button>

                {menuOpen && (
                  <div className="absolute right-0 sm:left-0 sm:right-auto mt-1 z-50 bg-white shadow-lg rounded-lg border border-ink-200 min-w-[180px] py-1 divide-y divide-ink-100 animate-fade-in">
                    <div className="py-1">
                      <button
                        onClick={() => {
                          setMenuOpen(false);
                          handleRunOcr();
                        }}
                        disabled={running || runningOcr}
                        className="w-full px-4 py-2 text-xs font-medium text-left text-ink-700 hover:bg-ink-50 flex items-center gap-2 disabled:opacity-50"
                      >
                        {runningOcr ? (
                          <>
                            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Running OCR Engine...
                          </>
                        ) : (
                          <>
                            <ScanLine className="h-3.5 w-3.5" /> Run OCR Engine
                          </>
                        )}
                      </button>
                      <button
                        onClick={() => {
                          setMenuOpen(false);
                          startPipelineStream();
                        }}
                        disabled={running || runningOcr}
                        className="w-full px-4 py-2 text-xs font-medium text-left text-ink-700 hover:bg-ink-50 flex items-center gap-2 disabled:opacity-50"
                      >
                        {running ? (
                          <>
                            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Running Verification Engine...
                          </>
                        ) : (
                          <>
                            <Play className="h-3.5 w-3.5 fill-current" /> Run Verification Engine
                          </>
                        )}
                      </button>
                    </div>
                    <div className="py-1">
                      <button
                        onClick={() => {
                          setMenuOpen(false);
                          handleDeleteCase();
                        }}
                        disabled={deleting}
                        className="w-full px-4 py-2 text-xs font-medium text-left text-rose-600 hover:bg-rose-50 flex items-center gap-2 disabled:opacity-50"
                      >
                        {deleting ? (
                          <>
                            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Deleting...
                          </>
                        ) : (
                          <>
                            <Trash2 className="h-3.5 w-3.5" /> Delete Case
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
          <div className="text-right">
            <p className="text-xs text-ink-500 uppercase tracking-wide">DGCL Confidence</p>
            <p className="text-2xl font-semibold text-ink-900 tabular-nums">
              {c.dgclScore.toFixed(1)}%
            </p>
          </div>
        </div>

        {/* Summary grid */}
        <dl className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-x-6 gap-y-3 mt-5 pt-5 border-t border-ink-100">
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
            <dt className="text-xs text-ink-500">Loan Type</dt>
            <dd className="text-sm font-medium text-ink-800 mt-0.5">{c.loanType}</dd>
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
        </div>
      </div>

      {/* Main layout */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-ink-800">DGCL Scorecard</h2>
          <label className="text-xs text-ink-600 flex items-center gap-1.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={showIssuesOnly}
              onChange={(e) => setShowIssuesOnly(e.target.checked)}
              className="rounded border-ink-300 text-brand-600 focus:ring-brand-500"
            />
            Show Issues Only
          </label>
        </div>
        <CaseReviewScorecard
          checkpoints={
            showIssuesOnly
              ? c.checkpoints.filter((cp) => cp.status === 'DISCREPANCY' || cp.status === 'INDETERMINATE')
              : c.checkpoints
          }
          onCheckpointClick={setDrawer}
        />
      </div>

      <CheckpointDrawer checkpoint={drawer} onClose={() => setDrawer(null)} />
      <UploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        caseId={c.id}
        onUploaded={load}
      />
      <PrintScorecardModal
        isOpen={printModalOpen}
        onClose={() => setPrintModalOpen(false)}
        caseData={c}
      />
    </div>
  );
}
