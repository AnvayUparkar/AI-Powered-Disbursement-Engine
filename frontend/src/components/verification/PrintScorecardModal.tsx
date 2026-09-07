import { useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Printer,
  Download,
  Loader2,
  X,
  ShieldCheck,
  AlertTriangle,
  MinusCircle,
  FileCheck,
  CheckCircle2,
  XCircle,
} from 'lucide-react';
import type { Case, Checkpoint, ComparisonResult } from '@/types';

interface PrintScorecardModalProps {
  isOpen: boolean;
  onClose: () => void;
  caseData: Case;
}

const formatCurrency = (val: number | null | undefined) => {
  if (val === null || val === undefined) return '—';
  return '₹' + Number(val).toLocaleString('en-IN');
};

const formatMethod = (method: string, matchType: string) => {
  if (!method && !matchType) return 'Direct Check';
  const m = (method || matchType).toLowerCase();
  if (m.includes('jaro_winkler')) return 'Jaro-Winkler (Fuzzy)';
  if (m.includes('equality') || m.includes('exact_string')) return 'Exact String Equality';
  if (m.includes('numeric') || m.includes('exact_numeric')) return 'Exact Numeric Match';
  if (m.includes('presence')) return 'Document Presence';
  if (m.includes('token_sort')) return 'Token Sort Ratio';
  return method.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
};

const formatSourceName = (src: string) => {
  if (!src) return '';
  const s = src.toLowerCase();
  if (s === 'los') return 'LOS Record';
  if (s === 'pan') return 'PAN Card';
  if (s === 'aadhaar') return 'Aadhaar';
  if (s === 'aadhaar_xml') return 'Aadhaar XML';
  if (s === 'application_form') return 'Application Form';
  if (s === 'loan_agreement') return 'Loan Agreement';
  if (s === 'kfs') return 'KFS';
  if (s === 'sanction_letter' || s === 'sanction') return 'Sanction Letter';
  if (s === 'account_statement') return 'Account Statement';
  if (s === 'bpi') return 'BPI';
  if (s === 'disbursal_memo' || s === 'memo') return 'Disbursal Memo';
  return src.replace(/_/g, ' ').toUpperCase();
};

const statusBadge = (status: Checkpoint['status']) => {
  switch (status) {
    case 'VERIFIED':
      return (
        <span className="inline-flex items-center gap-1 font-semibold text-xs px-2.5 py-0.5 rounded-full bg-emerald-100 text-emerald-800 border border-emerald-300">
          <CheckCircle2 className="h-3 w-3" /> VERIFIED
        </span>
      );
    case 'DISCREPANCY':
      return (
        <span className="inline-flex items-center gap-1 font-semibold text-xs px-2.5 py-0.5 rounded-full bg-rose-100 text-rose-800 border border-rose-300">
          <XCircle className="h-3 w-3" /> DISCREPANCY
        </span>
      );
    case 'INDETERMINATE':
      return (
        <span className="inline-flex items-center gap-1 font-semibold text-xs px-2.5 py-0.5 rounded-full bg-amber-100 text-amber-800 border border-amber-300">
          <AlertTriangle className="h-3 w-3" /> REVIEW
        </span>
      );
    case 'NOT_APPLICABLE':
    default:
      return (
        <span className="inline-flex items-center gap-1 font-semibold text-xs px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-300">
          <MinusCircle className="h-3 w-3" /> N/A
        </span>
      );
  }
};

const matchStatusBadge = (status: string) => {
  const s = (status || '').toUpperCase();
  if (s === 'MATCH') {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-bold px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200">
        MATCH
      </span>
    );
  }
  if (s === 'MISMATCH') {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-bold px-2 py-0.5 rounded bg-rose-50 text-rose-700 border border-rose-200">
        MISMATCH
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-[11px] font-bold px-2 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200">
      REVIEW
    </span>
  );
};

interface ScorecardContentProps {
  caseData: Case;
  displayedComparisons: ComparisonResult[];
  idPrefix?: string;
}

function ScorecardReportContent({ caseData, displayedComparisons, idPrefix = 'preview' }: ScorecardContentProps) {
  return (
    <div id={`${idPrefix}-scorecard-content`} className="bg-white text-slate-900 p-6 font-sans">
      {/* Header with HDB Logo */}
      <div className="flex items-center justify-between pb-5 border-b-2 border-slate-800">
        <div className="flex items-center gap-4">
          <img
            src="/hdb.png"
            alt="HDB Financial Services"
            className="h-16 w-auto object-contain"
          />
          <div>
            <h1 className="text-xl font-bold tracking-tight text-slate-900">
              HDB FINANCIAL SERVICES LIMITED
            </h1>
            <p className="text-xs uppercase tracking-wider font-semibold text-slate-600">
              Automated Disbursal Verification Scorecard (DGCL Engine)
            </p>
            <p className="text-[11px] text-slate-500 mt-0.5">
              Digital Governance & Compliance Layer · Multi-Tier Verification Audit
            </p>
          </div>
        </div>
        <div className="text-right">
          <div className="inline-block text-left bg-slate-50 border border-slate-200 rounded-lg p-3">
            <p className="text-[10px] uppercase font-bold text-slate-500">Case Verdict</p>
            <div className="mt-1">
              {statusBadge(caseData.status as Checkpoint['status'])}
            </div>
            <p className="text-xs font-semibold text-slate-800 mt-1">
              DGCL Score: <span className="font-bold text-brand-700">{caseData.dgclScore.toFixed(1)}%</span>
            </p>
          </div>
        </div>
      </div>

      {/* Case Metadata Grid */}
      <div className="grid grid-cols-4 gap-4 py-4 my-4 bg-slate-50/80 border border-slate-200 rounded-lg px-4 text-xs">
        <div>
          <span className="text-slate-500 font-medium block">Application ID:</span>
          <span className="font-semibold text-slate-800">{caseData.applicationId}</span>
        </div>
        <div>
          <span className="text-slate-500 font-medium block">Case ID:</span>
          <span className="font-semibold text-slate-800">{caseData.id}</span>
        </div>
        <div>
          <span className="text-slate-500 font-medium block">Applicant Name:</span>
          <span className="font-bold text-slate-900">{caseData.applicant}</span>
        </div>
        <div>
          <span className="text-slate-500 font-medium block">Loan Type:</span>
          <span className="font-semibold text-slate-800">{caseData.loanType}</span>
        </div>
        <div>
          <span className="text-slate-500 font-medium block">Loan Amount:</span>
          <span className="font-bold text-slate-900 tabular-nums">
            {formatCurrency(caseData.loanAmount)}
          </span>
        </div>
        <div>
          <span className="text-slate-500 font-medium block">Documents Ingested:</span>
          <span className="font-semibold text-slate-800">{caseData.documentCount} Unique Documents</span>
        </div>
        <div>
          <span className="text-slate-500 font-medium block">Processing Duration:</span>
          <span className="font-semibold text-slate-800">{caseData.processingTime}</span>
        </div>
        <div>
          <span className="text-slate-500 font-medium block">Report Timestamp:</span>
          <span className="font-semibold text-slate-800">{caseData.lastUpdated}</span>
        </div>
      </div>

      {/* Section 1: 12-Point Scorecard Table */}
      <div className="mt-6 print-avoid-break">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-bold uppercase tracking-wider text-slate-900 flex items-center gap-1.5">
            <ShieldCheck className="h-4 w-4 text-brand-600" /> Section 1: 12-Point DGCL Verification Scorecard
          </h2>
          <span className="text-xs text-slate-500 font-medium">
            12 System Checkpoints Evaluated
          </span>
        </div>

        <table className="w-full border border-slate-200 text-left text-xs">
          <thead className="bg-slate-100 border-b border-slate-200">
            <tr>
              <th className="py-2 px-3 font-bold text-slate-700 w-12 text-center">#</th>
              <th className="py-2 px-3 font-bold text-slate-700 w-44">Checkpoint</th>
              <th className="py-2 px-3 font-bold text-slate-700 w-32 text-center">Verdict</th>
              <th className="py-2 px-3 font-bold text-slate-700 w-24 text-right">Confidence</th>
              <th className="py-2 px-3 font-bold text-slate-700">Rule & Validation Summary</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {caseData.checkpoints.map((cp, idx) => (
              <tr key={cp.id} className={idx % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}>
                <td className="py-2 px-3 font-mono text-center text-slate-500">
                  {String(cp.id).padStart(2, '0')}
                </td>
                <td className="py-2 px-3 font-semibold text-slate-900">{cp.name}</td>
                <td className="py-2 px-3 text-center">{statusBadge(cp.status)}</td>
                <td className="py-2 px-3 text-right font-mono font-medium text-slate-700">
                  {cp.confidence > 0 ? `${cp.confidence.toFixed(1)}%` : '—'}
                </td>
                <td className="py-2 px-3 text-slate-600 leading-tight">
                  <p className="font-medium text-slate-800">{cp.reason}</p>
                  {cp.rule && (
                    <p className="text-[11px] text-slate-500 italic mt-0.5">{cp.rule}</p>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Section 2: Detailed Field-Level Comparison & Verification Logic */}
      <div className="mt-8 print-avoid-break">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-bold uppercase tracking-wider text-slate-900 flex items-center gap-1.5">
            <FileCheck className="h-4 w-4 text-brand-600" /> Section 2: Field-Level Comparison Audit & Verification Logic
          </h2>
          <span className="text-xs text-slate-500 font-medium">
            Surfacing Comparison Results ({displayedComparisons.length} Checks)
          </span>
        </div>

        {displayedComparisons.length === 0 ? (
          <div className="p-4 border border-slate-200 rounded text-center text-xs text-slate-500">
            No comparison records matching current filter.
          </div>
        ) : (
          <table className="w-full border border-slate-200 text-left text-xs">
            <thead className="bg-slate-100 border-b border-slate-200">
              <tr>
                <th className="py-2 px-3 font-bold text-slate-700 w-36">Field</th>
                <th className="py-2 px-3 font-bold text-slate-700 w-44">Compared Sources</th>
                <th className="py-2 px-3 font-bold text-slate-700">Extracted & Compared Values</th>
                <th className="py-2 px-3 font-bold text-slate-700 w-40">Logic / Method</th>
                <th className="py-2 px-3 font-bold text-slate-700 w-24 text-center">Result</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {displayedComparisons.map((c, idx) => {
                const srcA = formatSourceName(c.sources?.[0] || '');
                const srcB = formatSourceName(c.sources?.[1] || '');
                const valA = c.values?.[0] !== undefined && c.values?.[0] !== null ? String(c.values[0]) : '—';
                const valB = c.values?.[1] !== undefined && c.values?.[1] !== null ? String(c.values[1]) : '—';

                return (
                  <tr key={c.check_id || idx} className={idx % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}>
                    <td className="py-2 px-3 align-top font-medium text-slate-900">
                      {c.field ? c.field.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase()) : 'Check'}
                      <span className="block text-[10px] text-slate-400 font-mono mt-0.5">
                        {c.subnode || 'checkpoint'}
                      </span>
                    </td>
                    <td className="py-2 px-3 align-top text-slate-700">
                      <span className="font-semibold text-slate-800">{srcA}</span>
                      <span className="text-slate-400 mx-1">vs</span>
                      <span className="font-semibold text-slate-800">{srcB}</span>
                    </td>
                    <td className="py-2 px-3 align-top text-slate-800 font-mono text-[11px] leading-relaxed">
                      <div className="flex items-center gap-1.5">
                        <span className="text-slate-400 text-[10px] w-14 shrink-0">{srcA}:</span>
                        <span className="font-semibold bg-slate-100 px-1 rounded">{valA}</span>
                      </div>
                      <div className="flex items-center gap-1.5 mt-0.5">
                        <span className="text-slate-400 text-[10px] w-14 shrink-0">{srcB}:</span>
                        <span className="font-semibold bg-slate-100 px-1 rounded">{valB}</span>
                      </div>
                      {c.notes && (
                        <p className="text-[10px] text-slate-500 font-sans mt-0.5 italic">{c.notes}</p>
                      )}
                    </td>
                    <td className="py-2 px-3 align-top text-slate-600">
                      <span className="font-medium text-slate-800">
                        {formatMethod(c.method, c.match_type)}
                      </span>
                      {c.confidence !== undefined && (
                        <span className="block text-[10px] text-slate-500 font-mono">
                          Fidelity: {(c.confidence * 100).toFixed(1)}%
                        </span>
                      )}
                    </td>
                    <td className="py-2 px-3 align-top text-center">
                      {matchStatusBadge(c.match_status)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Footer Disclaimer */}
      <div className="mt-8 pt-4 border-t border-slate-300 text-[10px] text-slate-500 flex justify-between items-center print-avoid-break">
        <p>
          Automated Disbursal Scorecard · HDB Financial Services Ltd. · All verification rules executed via DGCL Engine.
        </p>
        <p className="font-mono">Page 1 of 1 · System Generated</p>
      </div>
    </div>
  );
}

export function PrintScorecardModal({ isOpen, onClose, caseData }: PrintScorecardModalProps) {
  const [filterMismatchOnly, setFilterMismatchOnly] = useState(false);
  const [downloading, setDownloading] = useState(false);

  if (!isOpen) return null;

  const comparisonList: ComparisonResult[] = caseData.comparisonResults || [];
  const displayedComparisons = filterMismatchOnly
    ? comparisonList.filter((c) => c.match_status === 'MISMATCH' || c.match_status === 'REVIEW')
    : comparisonList;

  const pdfFilename = `${caseData.applicationId || caseData.id}_scorecard.pdf`;

  const handleDownloadPdf = async () => {
    setDownloading(true);
    const element = document.getElementById('preview-scorecard-content');
    if (!element) {
      setDownloading(false);
      handlePrint();
      return;
    }

    const opt = {
      margin: [8, 8, 8, 8] as [number, number, number, number],
      filename: pdfFilename,
      image: { type: 'jpeg' as const, quality: 0.98 },
      html2canvas: { scale: 2, useCORS: true, logging: false },
      jsPDF: { unit: 'mm' as const, format: 'a4' as const, orientation: 'portrait' as const },
      pagebreak: { mode: ['avoid-all', 'css', 'legacy'] },
    };

    try {
      // @ts-ignore
      const html2pdfModule = await import('html2pdf.js');
      const html2pdf = html2pdfModule.default || html2pdfModule;
      await html2pdf().set(opt).from(element).save();
    } catch (err) {
      console.error('Direct PDF export error, falling back to window.print:', err);
      handlePrint();
    } finally {
      setDownloading(false);
    }
  };

  const handlePrint = () => {
    const originalTitle = document.title;
    document.title = `${caseData.applicationId || caseData.id}_scorecard`;
    window.print();
    setTimeout(() => {
      document.title = originalTitle;
    }, 1500);
  };

  return (
    <>
      {/* On-screen Preview Modal */}
      <div className="fixed inset-0 z-50 overflow-y-auto bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 no-print">
        <div className="relative bg-white rounded-xl shadow-2xl max-w-5xl w-full max-h-[92vh] flex flex-col overflow-hidden">
          {/* Top Control Bar */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-ink-200 bg-ink-50/80 shrink-0">
            <div className="flex items-center gap-2">
              <FileCheck className="h-5 w-5 text-brand-600" />
              <div>
                <h2 className="text-base font-semibold text-ink-900">
                  Disbursal Verification Scorecard
                </h2>
                <p className="text-[11px] text-ink-500 font-mono">
                  Target file: {pdfFilename}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <label className="text-xs text-ink-600 flex items-center gap-1.5 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={filterMismatchOnly}
                  onChange={(e) => setFilterMismatchOnly(e.target.checked)}
                  className="rounded border-ink-300 text-brand-600 focus:ring-brand-500"
                />
                Show Discrepancies Only
              </label>
              <button
                onClick={handleDownloadPdf}
                disabled={downloading}
                className="btn btn-primary inline-flex items-center gap-2 text-xs font-semibold py-1.5 px-4 rounded-lg shadow-sm hover:shadow transition-all"
                title={`Directly download ${pdfFilename}`}
              >
                {downloading ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" /> Generating PDF...
                  </>
                ) : (
                  <>
                    <Download className="h-4 w-4" /> Download PDF
                  </>
                )}
              </button>
              <button
                onClick={handlePrint}
                className="btn btn-secondary inline-flex items-center gap-2 text-xs font-semibold py-1.5 px-3 rounded-lg shadow-sm hover:shadow transition-all"
                title="Open system print dialog with pre-filled filename"
              >
                <Printer className="h-4 w-4" /> Print Dialog
              </button>
              <button
                onClick={onClose}
                className="p-1.5 rounded-lg text-ink-400 hover:text-ink-700 hover:bg-ink-100 transition-colors"
                aria-label="Close"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
          </div>

          {/* Scrollable Preview Area */}
          <div className="flex-1 overflow-y-auto">
            <ScorecardReportContent
              caseData={caseData}
              displayedComparisons={displayedComparisons}
              idPrefix="preview"
            />
          </div>
        </div>
      </div>

      {/* Direct Portal on document.body for clean, unclipped multi-page printing */}
      {createPortal(
        <div id="printable-scorecard-portal">
          <ScorecardReportContent
            caseData={caseData}
            displayedComparisons={displayedComparisons}
            idPrefix="print"
          />
        </div>,
        document.body,
      )}
    </>
  );
}

