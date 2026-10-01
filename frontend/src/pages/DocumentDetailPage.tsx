import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, FileText, Sparkles, PanelRight, PanelRightClose, X } from 'lucide-react';
import { DocumentViewer } from '@/components/documents/DocumentViewer';
import { ProcessingPipeline } from '@/components/documents/ProcessingPipeline';
import { OcrEngineBadge } from '@/components/documents/OcrEngineBadge';
import { DocumentTimingPanel } from '@/components/pipeline/TimingBreakdown';
import { Skeleton, CardSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/ui/ErrorState';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';
import { documentsService } from '@/services';
import type { DocumentRecord } from '@/types';

export default function DocumentDetailPage() {
  const { documentId } = useParams();
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = () => {
    if (!documentId) return;
    setLoading(true);
    setError(false);
    documentsService.getById(documentId).then(setDoc).catch(() => setError(true)).finally(() => setLoading(false));
  };

  useEffect(load, [documentId]);

  // Auto-poll while OCR or extraction is actively processing
  useEffect(() => {
    if (!doc || (doc.ocrStatus !== 'PROCESSING' && doc.extractionStatus !== 'PROCESSING')) return;
    const interval = setInterval(() => {
      if (!documentId) return;
      documentsService.getById(documentId).then((updated) => {
        if (updated) setDoc(updated);
      }).catch(() => {});
    }, 2500);
    return () => clearInterval(interval);
  }, [doc?.ocrStatus, doc?.extractionStatus, documentId]);

  if (loading) {
    return (
      <div>
        <Skeleton className="h-4 w-32 mb-4" />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 h-[500px]"><CardSkeleton /></div>
          <CardSkeleton />
        </div>
      </div>
    );
  }

  if (error || !doc) {
    return (
      <div>
        <Link to="/documents" className="inline-flex items-center gap-1.5 text-sm text-brand-600 hover:text-brand-700 mb-4">
          <ArrowLeft className="h-4 w-4" /> Back to Documents
        </Link>
        <ErrorState title="Unable to load document" onRetry={load} />
      </div>
    );
  }

  return (
    <div>
      <Link to="/documents" className="inline-flex items-center gap-1.5 text-sm text-brand-600 hover:text-brand-700 mb-4">
        <ArrowLeft className="h-4 w-4" /> Back to Documents
      </Link>

      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-3">
          <div className="rounded-md bg-ink-100 p-2"><FileText className="h-5 w-5 text-ink-500" /></div>
          <div>
            <h1 className="text-lg font-semibold text-ink-900">{doc.name}</h1>
            <p className="text-xs text-ink-500">{doc.type} · {doc.pages} pages · {(doc.sizeKb / 1024).toFixed(1)} MB · Case <Link to={`/cases/${doc.caseId}`} className="text-brand-600 hover:text-brand-700">{doc.caseId}</Link></p>
          </div>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <div>
            <p className="text-xs text-ink-500">Confidence</p>
            <div className="w-32 mt-0.5"><ConfidenceBar value={doc.confidence} /></div>
          </div>
          <OcrEngineBadge info={doc.ocrEngine} />
          {doc.vlmUsed && (
            <span className="chip bg-review-50 text-review-700"><Sparkles className="h-3.5 w-3.5" /> VLM used</span>
          )}
          <button
            type="button"
            onClick={() => setSidebarOpen((v) => !v)}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border text-xs font-medium transition-colors ${
              sidebarOpen
                ? 'bg-brand-50 border-brand-300 text-brand-700 hover:bg-brand-100 shadow-2xs'
                : 'bg-white border-ink-200 text-ink-700 hover:bg-ink-50 shadow-2xs'
            }`}
            aria-expanded={sidebarOpen}
            title={sidebarOpen ? 'Hide Pipeline & Timing' : 'Show Pipeline & Timing'}
          >
            {sidebarOpen ? <PanelRightClose className="h-4 w-4" /> : <PanelRight className="h-4 w-4" />}
            <span>{sidebarOpen ? 'Hide Pipeline & Timing' : 'Pipeline & Timing'}</span>
          </button>
        </div>
      </div>

      <div className="flex flex-col lg:flex-row gap-5 items-start">
        <div className={`transition-all duration-200 w-full ${sidebarOpen ? 'lg:flex-1 min-w-0' : 'w-full'}`}>
          <div className="h-[600px] lg:h-[750px]">
            <DocumentViewer document={doc} />
          </div>
        </div>

        {sidebarOpen && (
          <aside className="w-full lg:w-96 shrink-0 space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-ink-200">
              <h2 className="text-sm font-semibold text-ink-900">Pipeline & Timing</h2>
              <button
                type="button"
                onClick={() => setSidebarOpen(false)}
                className="p-1 rounded text-ink-400 hover:text-ink-600 hover:bg-ink-100 transition-colors"
                aria-label="Close panel"
                title="Close panel"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <ProcessingPipeline steps={doc.processingSteps} />
            <DocumentTimingPanel timing={doc.timing} />
          </aside>
        )}
      </div>
    </div>
  );
}
