import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, FileText, Sparkles } from 'lucide-react';
import { DocumentViewer } from '@/components/documents/DocumentViewer';
import { ProcessingPipeline } from '@/components/documents/ProcessingPipeline';
import { Skeleton, CardSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/ui/ErrorState';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';
import { documentsService } from '@/services';
import type { DocumentRecord } from '@/types';

export default function DocumentDetailPage() {
  const { documentId } = useParams();
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = () => {
    if (!documentId) return;
    setLoading(true);
    setError(false);
    documentsService.getById(documentId).then(setDoc).catch(() => setError(true)).finally(() => setLoading(false));
  };

  useEffect(load, [documentId]);

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
        <div className="flex items-center gap-4 text-sm">
          <div>
            <p className="text-xs text-ink-500">Confidence</p>
            <div className="w-32 mt-0.5"><ConfidenceBar value={doc.confidence} /></div>
          </div>
          {doc.vlmUsed && (
            <span className="chip bg-review-50 text-review-700"><Sparkles className="h-3.5 w-3.5" /> VLM used</span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 h-[600px]">
          <DocumentViewer document={doc} />
        </div>
        <div>
          <ProcessingPipeline steps={doc.processingSteps} />
        </div>
      </div>
    </div>
  );
}
