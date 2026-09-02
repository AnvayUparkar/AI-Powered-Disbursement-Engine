import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  X,
  UploadCloud,
  FileText,
  CheckCircle2,
  Loader2,
  AlertCircle,
  Sparkles,
  ArrowRight,
  ShieldCheck,
  FolderPlus,
} from 'lucide-react';
import type { DocumentType } from '@/types';
import { node2Api } from '@/api/node2';
import { casesService } from '@/services/cases';

const DOC_TYPES: DocumentType[] = [
  'Application Form',
  'PAN',
  'Aadhaar',
  'KYC',
  'KFS',
  'Sanction Letter',
  'Loan Agreement',
  'Disbursal Memo',
  'BT Details',
  'Aadhaar XML',
  'VKYC Audit Trail',
  'Miscellaneous',
];

type FileStatus = 'QUEUED' | 'UPLOADING' | 'DONE' | 'FAILED';

interface QueuedFile {
  id: string;
  file: File;
  docType: DocumentType;
  status: FileStatus;
  progress: number;
}

let fileCounter = 0;

export function CreateCaseModal({
  open,
  onClose,
  onCaseCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCaseCreated?: (caseId: string) => void;
}) {
  const navigate = useNavigate();
  const [caseId, setCaseId] = useState('');
  const [loadingNextId, setLoadingNextId] = useState(true);
  const [dragging, setDragging] = useState(false);
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setLoadingNextId(true);
      casesService
        .getNextCaseId()
        .then((id) => setCaseId(id))
        .catch(() => setCaseId('LOAN_004'))
        .finally(() => setLoadingNextId(false));
    } else {
      setQueue([]);
      setDragging(false);
      setStatusMessage(null);
      setSubmitting(false);
    }
  }, [open]);

  const guessType = (name: string): DocumentType => {
    const n = name.toLowerCase();
    if (n.includes('app') || n.includes('application')) return 'Application Form';
    if (n.includes('pan')) return 'PAN';
    if ((n.includes('aadhaar') || n.includes('aadhar') || n.includes('adhar')) && n.includes('xml')) return 'Aadhaar XML';
    if (n.includes('aadhaar') || n.includes('aadhar') || n.includes('adhar')) return 'Aadhaar';
    if (n.includes('kyc')) return 'KYC';
    if (n.includes('kfs')) return 'KFS';
    if (n.includes('sanction')) return 'Sanction Letter';
    if (n.includes('agreement')) return 'Loan Agreement';
    if (n.includes('memo') || n.includes('disbursal')) return 'Disbursal Memo';
    if (n.includes('bt') || n.includes('foreclosure')) return 'BT Details';
    if (n.includes('vky')) return 'VKYC Audit Trail';
    return 'Miscellaneous';
  };

  const addFiles = useCallback((files: FileList | File[]) => {
    const arr = Array.from(files);
    const mapped: QueuedFile[] = arr.map((file) => ({
      id: `new-case-file-${++fileCounter}`,
      file,
      docType: guessType(file.name),
      status: 'QUEUED',
      progress: 0,
    }));
    setQueue((q) => [...q, ...mapped]);
  }, []);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  };

  const handleCreateAndRun = async () => {
    if (!caseId) return;
    try {
      setSubmitting(true);
      setStatusMessage(`Initializing case ${caseId}...`);

      // 1. Create the case record in backend
      await casesService.createCase({
        case_id: caseId,
        applicant_name: 'Applicant',
        loan_type: 'Personal Loan',
      });

      // 2. Upload and OCR each queued document
      const pendingFiles = queue.filter((f) => f.status === 'QUEUED');
      for (const [index, qf] of pendingFiles.entries()) {
        setStatusMessage(`Uploading & OCR processing ${index + 1}/${pendingFiles.length}: ${qf.file.name}...`);
        setQueue((q) => q.map((f) => (f.id === qf.id ? { ...f, status: 'UPLOADING', progress: 50 } : f)));

        try {
          const docId = `DOC-${caseId}-${Date.now().toString().slice(-4)}-${index + 1}`;
          await node2Api.uploadAndProcess(
            qf.file,
            docId,
            undefined,
            caseId,
            qf.docType,
          );
          setQueue((q) => q.map((f) => (f.id === qf.id ? { ...f, status: 'DONE', progress: 100 } : f)));
        } catch (err) {
          console.error('File upload error:', err);
          setQueue((q) => q.map((f) => (f.id === qf.id ? { ...f, status: 'FAILED', progress: 0 } : f)));
        }
      }

      setStatusMessage('Starting LangGraph verification pipeline...');
      onCaseCreated?.(caseId);
      onClose();
      // Navigate straight to the new case detail view with autoRun enabled
      navigate(`/cases/${caseId}?autoRun=true`);
    } catch (err: any) {
      console.error('Failed creating case and initiating pipeline:', err);
      setStatusMessage('Error creating case. Please try again.');
      setSubmitting(false);
    }
  };

  const removeFile = (id: string) => setQueue((q) => q.filter((f) => f.id !== id));
  const setFileType = (id: string, docType: DocumentType) =>
    setQueue((q) => q.map((f) => (f.id === id ? { ...f, docType } : f)));

  if (!open) return null;

  const hasQueued = queue.length > 0;

  return (
    <>
      <div className="fixed inset-0 z-50 bg-ink-950/50 backdrop-blur-sm animate-fade-in" onClick={onClose} aria-hidden />
      <div
        className="fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 w-full max-w-2xl max-h-[90vh] flex flex-col bg-white rounded-xl shadow-2xl border border-ink-200 animate-fade-in"
        role="dialog"
        aria-label="Create New Loan Case"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-ink-200 bg-gradient-to-r from-brand-50/50 to-sky-50/30 shrink-0">
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-brand-600 text-white p-2 shadow-sm">
              <FolderPlus className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-ink-900">Create New Loan Case</h2>
              <p className="text-xs text-ink-500">
                Upload documents to automatically extract metadata and run DGCL verification.
              </p>
            </div>
          </div>
          <button onClick={onClose} className="btn-ghost p-1.5" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
          {/* Auto-assigned Case ID Card */}
          <div className="rounded-lg border border-brand-200 bg-brand-50/40 p-3.5 flex items-center justify-between">
            <div>
              <span className="text-xs font-semibold uppercase tracking-wider text-brand-700">Auto-Assigned Case ID</span>
              <div className="flex items-center gap-2 mt-0.5">
                <span className="font-mono text-lg font-bold text-ink-900">{loadingNextId ? 'Generating...' : caseId}</span>
                <span className="chip bg-brand-100 text-brand-800 text-[11px] font-medium">Auto-Sequenced</span>
              </div>
            </div>
            <div className="text-right text-xs text-ink-500 max-w-[240px]">
              Applicant, loan amount, and tenure will be extracted automatically from uploaded documents.
            </div>
          </div>

          {/* Drop Zone */}
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
            className={`rounded-xl border-2 border-dashed p-7 text-center cursor-pointer transition-all ${
              dragging
                ? 'border-brand-500 bg-brand-50/60 scale-[0.99]'
                : 'border-ink-300 hover:border-brand-500 hover:bg-brand-50/20'
            }`}
          >
            <input
              ref={inputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => { if (e.target.files?.length) addFiles(e.target.files); e.target.value = ''; }}
              accept=".pdf,.jpg,.jpeg,.png,.zip,.tiff,.bmp"
            />
            <UploadCloud className="h-10 w-10 mx-auto text-brand-500 mb-2" />
            <p className="text-sm font-semibold text-ink-800">
              {dragging ? 'Drop documents here' : 'Drag & drop loan documents or click to browse'}
            </p>
            <p className="text-xs text-ink-500 mt-1">
              Supports Application Form, PAN, Aadhaar XML, KFS, Sanction Letter, Agreement, and Memos (PDF, JPG, PNG, ZIP)
            </p>
          </div>

          {/* Queue List */}
          {queue.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                  {queue.length} document{queue.length > 1 ? 's' : ''} ready to process
                </p>
              </div>

              {queue.map((f) => (
                <div key={f.id} className="card p-3 flex items-center gap-3 border border-ink-200">
                  <FileText className="h-5 w-5 text-brand-600 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium text-ink-800 truncate">{f.file.name}</p>
                      <span className="text-xs text-ink-500 tabular-nums shrink-0">
                        {(f.file.size / 1024).toFixed(0)} KB
                      </span>
                    </div>
                    <div className="flex items-center gap-2 mt-1.5">
                      <select
                        value={f.docType}
                        onChange={(e) => setFileType(f.id, e.target.value as DocumentType)}
                        className="select py-1 text-xs w-44"
                        disabled={submitting}
                        aria-label="Document type"
                      >
                        {DOC_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                      {f.status === 'UPLOADING' && (
                        <div className="flex-1 flex items-center gap-2">
                          <div className="flex-1 h-1.5 rounded-full bg-ink-100 overflow-hidden">
                            <div
                              className="h-full bg-brand-500 rounded-full transition-all duration-200"
                              style={{ width: `${f.progress}%` }}
                            />
                          </div>
                          <span className="font-mono text-[11px] text-ink-500 tabular-nums">
                            {Math.round(f.progress)}%
                          </span>
                        </div>
                      )}
                      {f.status === 'DONE' && (
                        <span className="chip bg-verified-50 text-verified-700">
                          <CheckCircle2 className="h-3.5 w-3.5" /> OCR Extracted
                        </span>
                      )}
                    </div>
                  </div>
                  {!submitting && f.status === 'QUEUED' && (
                    <button
                      onClick={() => removeFile(f.id)}
                      className="btn-ghost p-1 text-ink-400 hover:text-discrepancy-600"
                      aria-label="Remove file"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  )}
                  {f.status === 'UPLOADING' && (
                    <Loader2 className="h-4 w-4 text-brand-500 animate-spin shrink-0" />
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Status / Notice */}
          {statusMessage && (
            <div className="flex items-center gap-2 p-3 rounded-lg bg-brand-50 border border-brand-200 text-xs font-medium text-brand-800 animate-pulse">
              <Loader2 className="h-4 w-4 animate-spin shrink-0" />
              <span>{statusMessage}</span>
            </div>
          )}

          {!statusMessage && (
            <div className="flex items-start gap-2 rounded-lg bg-slate-50 border border-slate-200 p-3 text-xs text-ink-600">
              <ShieldCheck className="h-4 w-4 text-brand-600 shrink-0 mt-0.5" />
              <p>
                Once initiated, the pipeline starts automatically from <strong>Node 1 (Fetch)</strong> through{' '}
                <strong>Node 2 (IDP OCR)</strong>, <strong>Node 3 (Comparison)</strong>, and generates the DGCL scorecard.
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-6 py-4 border-t border-ink-200 bg-ink-50/50 shrink-0">
          <button onClick={onClose} disabled={submitting} className="btn-secondary">
            Cancel
          </button>
          <button
            onClick={handleCreateAndRun}
            disabled={submitting || !caseId}
            className="btn-primary inline-flex items-center gap-2 px-4 py-2 font-semibold shadow-sm"
          >
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> Ingesting & Starting Pipeline...
              </>
            ) : (
              <>
                <Sparkles className="h-4 w-4" /> Create Case & Run Pipeline ({queue.length} docs)
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </button>
        </div>
      </div>
    </>
  );
}
