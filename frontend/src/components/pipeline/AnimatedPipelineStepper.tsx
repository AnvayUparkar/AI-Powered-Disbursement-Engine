import { useEffect, useState } from 'react';
import {
  FileDown,
  FileSearch,
  GitFork,
  FileCheck2,
  ShieldAlert,
  Award,
  SendHorizontal,
  CheckCircle2,
  Loader2,
  AlertTriangle,
  Clock,
  Sparkles,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import type { PipelineStage } from '@/types';

interface StageConfig {
  key: string;
  name: string;
  shortLabel: string;
  icon: typeof FileDown;
  description: string;
}

const STAGES: StageConfig[] = [
  {
    key: 'fetch',
    name: 'Node 1: Fetch & Ingest',
    shortLabel: 'Fetch',
    icon: FileDown,
    description: 'Retrieving LOS application metadata & ingesting raw documents from DMS/S3.',
  },
  {
    key: 'extract',
    name: 'Node 2: IDP & Extraction',
    shortLabel: 'IDP OCR',
    icon: FileSearch,
    description: 'Docling layout analysis, RapidOCR PP-OCRv6 text extraction & VLM quality router.',
  },
  {
    key: 'comparison',
    name: 'Node 3: Comparison Fan-Out',
    shortLabel: 'Comparison',
    icon: GitFork,
    description: 'Parallel subnodes 3a (KYC), 3b (KFS & Sanction), and 3c (Topup/BT) rule matching.',
  },
  {
    key: 'compile',
    name: 'Node 4: Compile Report',
    shortLabel: 'Compile',
    icon: FileCheck2,
    description: 'Aggregating field discrepancies, match proofs, and subnode rollups into compiled report.',
  },
  {
    key: 'checker',
    name: 'Node Checker: Gate Validator',
    shortLabel: 'Checker',
    icon: ShieldAlert,
    description: 'Verifying data consistency, hard gates, and determining retry vs proceed routing.',
  },
  {
    key: 'scorecard',
    name: 'Node 5: DGCL Scorecard',
    shortLabel: 'Scorecard',
    icon: Award,
    description: 'Evaluating 12 DGCL checkpoints, computing risk scores, and generating adjudication.',
  },
  {
    key: 'push',
    name: 'Node 6: Push & Finalize',
    shortLabel: 'Push',
    icon: SendHorizontal,
    description: 'Pushing disbursement decision to LOS, updating audit trails, and completing case.',
  },
];

interface AnimatedPipelineStepperProps {
  currentStage?: PipelineStage;
  completedStages?: string[];
  subnodeRollups?: Record<string, string>;
  isRunning?: boolean;
  errors?: string[];
  onFinish?: () => void;
  title?: string;
  className?: string;
}

export function AnimatedPipelineStepper({
  currentStage = 'fetch',
  completedStages = [],
  subnodeRollups = {},
  isRunning = true,
  errors = [],
  onFinish,
  title = 'LangGraph Verification Engine Execution',
  className = '',
}: AnimatedPipelineStepperProps) {
  const [elapsed, setElapsed] = useState(0);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let timer: any = null;
    if (isRunning) {
      const start = Date.now();
      timer = setInterval(() => {
        setElapsed(Math.floor((Date.now() - start) / 1000));
      }, 1000);
    }
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [isRunning]);

  const activeStageIndex = STAGES.findIndex((s) => s.key === currentStage);
  const completedCount = completedStages.length;
  const progressPct = Math.min(100, Math.round((completedCount / STAGES.length) * 100));
  const isDone = currentStage === 'finish' || completedStages.length === STAGES.length || !isRunning;

  return (
    <div className={`card overflow-hidden border border-brand-500/30 bg-gradient-to-br from-white via-brand-50/20 to-sky-50/30 shadow-md ${className}`}>
      {/* Top Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3.5 border-b border-ink-100 bg-white/80 backdrop-blur">
        <div className="flex items-center gap-2.5">
          <div className={`rounded-lg p-2 ${isDone ? 'bg-verified-500/10 text-verified-600' : 'bg-brand-500/10 text-brand-600'}`}>
            {isDone ? <Sparkles className="h-5 w-5 animate-pulse" /> : <Loader2 className="h-5 w-5 animate-spin" />}
          </div>
          <div>
            <h3 className="text-sm font-semibold text-ink-900 flex items-center gap-2">
              {title}
              {isDone ? (
                <span className="chip bg-verified-50 text-verified-700 text-[11px] font-medium border border-verified-200">
                  <CheckCircle2 className="h-3 w-3" /> Complete
                </span>
              ) : (
                <span className="chip bg-brand-50 text-brand-700 text-[11px] font-medium border border-brand-200 animate-pulse">
                  <Loader2 className="h-3 w-3 animate-spin" /> Processing
                </span>
              )}
            </h3>
            <p className="text-xs text-ink-500">
              {isDone
                ? 'All 7 LangGraph nodes evaluated successfully.'
                : `Currently executing: ${STAGES[activeStageIndex]?.name || 'Pipeline initializing...'}`}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-xs font-mono text-ink-600 bg-ink-100/80 px-2.5 py-1 rounded-md">
            <Clock className="h-3.5 w-3.5 text-ink-400" />
            <span>{elapsed}s</span>
          </div>

          <button
            onClick={() => setExpanded(!expanded)}
            className="btn-ghost p-1.5 text-ink-500 hover:text-ink-800 text-xs flex items-center gap-1"
            aria-label="Toggle pipeline details"
          >
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
        </div>
      </div>

      {/* Progress Bar */}
      <div className="h-1.5 w-full bg-ink-100 overflow-hidden">
        <div
          className={`h-full transition-all duration-500 ease-out ${
            isDone ? 'bg-verified-500' : 'bg-gradient-to-r from-brand-500 to-sky-500'
          }`}
          style={{ width: `${progressPct}%` }}
        />
      </div>

      {/* Stepper Horizontal Flow */}
      <div className="p-5">
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
          {STAGES.map((stage, idx) => {
            const Icon = stage.icon;
            const isCompleted = completedStages.includes(stage.key) || (isDone && idx <= activeStageIndex);
            const isActive = !isCompleted && stage.key === currentStage && isRunning;

            return (
              <div
                key={stage.key}
                className={`relative rounded-lg p-3 transition-all duration-200 border flex flex-col justify-between ${
                  isActive
                    ? 'bg-white border-brand-500 shadow-md ring-2 ring-brand-500/20 scale-[1.02]'
                    : isCompleted
                    ? 'bg-emerald-50/40 border-verified-200 text-ink-800'
                    : 'bg-ink-50/60 border-ink-200 text-ink-400 opacity-75'
                }`}
              >
                {/* Node Status Header */}
                <div className="flex items-center justify-between gap-1 mb-2">
                  <div
                    className={`rounded-md p-1.5 ${
                      isActive
                        ? 'bg-brand-500 text-white shadow-sm'
                        : isCompleted
                        ? 'bg-verified-600 text-white'
                        : 'bg-ink-200 text-ink-500'
                    }`}
                  >
                    <Icon className="h-4 w-4" />
                  </div>

                  {isCompleted ? (
                    <CheckCircle2 className="h-4 w-4 text-verified-600 shrink-0" />
                  ) : isActive ? (
                    <Loader2 className="h-4 w-4 text-brand-600 animate-spin shrink-0" />
                  ) : (
                    <span className="text-[10px] font-mono text-ink-400 font-medium">#{idx + 1}</span>
                  )}
                </div>

                {/* Node Label */}
                <div>
                  <p className={`text-xs font-semibold truncate ${isActive ? 'text-brand-700' : isCompleted ? 'text-ink-900' : 'text-ink-500'}`}>
                    {stage.shortLabel}
                  </p>
                  <p className="text-[11px] text-ink-400 leading-tight mt-0.5 line-clamp-1">
                    {isActive ? 'Processing...' : isCompleted ? 'Completed' : 'Pending'}
                  </p>
                </div>

                {/* Subnode chips for Node 3 */}
                {stage.key === 'comparison' && (isActive || isCompleted) && Object.keys(subnodeRollups).length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2 pt-2 border-t border-ink-100">
                    {Object.entries(subnodeRollups).map(([subKey, subVal]) => (
                      <span
                        key={subKey}
                        className={`text-[9px] px-1 py-0.5 rounded font-mono font-medium ${
                          subVal === 'Verified' || subVal === 'MATCH'
                            ? 'bg-verified-100 text-verified-800'
                            : 'bg-amber-100 text-amber-800'
                        }`}
                      >
                        {subKey.replace('loan_', '').replace('kfs_', '').toUpperCase()}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Detailed Description Drawer */}
        {expanded && (
          <div className="mt-4 pt-4 border-t border-ink-200/70 grid grid-cols-1 md:grid-cols-2 gap-3 text-xs animate-fade-in">
            {STAGES.map((stg) => (
              <div key={stg.key} className="p-2.5 rounded-md bg-white border border-ink-100 flex items-start gap-2.5">
                <stg.icon className="h-4 w-4 text-brand-600 mt-0.5 shrink-0" />
                <div>
                  <p className="font-semibold text-ink-800">{stg.name}</p>
                  <p className="text-ink-500 mt-0.5 text-[11px] leading-relaxed">{stg.description}</p>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Errors list if any */}
        {errors.length > 0 && (
          <div className="mt-3 p-3 rounded-md bg-amber-50 border border-amber-300 text-xs text-amber-900 space-y-1">
            <div className="flex items-center gap-1.5 font-semibold text-amber-800">
              <AlertTriangle className="h-4 w-4 text-amber-600" />
              <span>Pipeline Warnings & Notifications</span>
            </div>
            {errors.map((err, i) => (
              <p key={i} className="pl-5 text-amber-700">{err}</p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
