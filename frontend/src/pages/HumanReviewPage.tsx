import { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { ArrowLeft, FileText, Check, X, Edit3, AlertTriangle, History } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { ConfidenceBar } from '@/components/ui/ConfidenceBar';
import { Skeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/ui/ErrorState';
import { reviewService, documentsService } from '@/services';
import type { ReviewItem, DocumentRecord } from '@/types';

export default function HumanReviewPage() {
  const { reviewId } = useParams();
  const navigate = useNavigate();
  const [item, setItem] = useState<ReviewItem | null>(null);
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const [decision, setDecision] = useState<'CONFIRM' | 'CORRECT' | 'REJECT' | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const load = () => {
    if (!reviewId) return;
    setLoading(true);
    setError(false);
    reviewService.getById(reviewId).then((r) => {
      setItem(r);
      if (r && r.documentId) {
        documentsService.getById(r.documentId).then(setDoc).catch(() => {});
      }
    }).catch(() => setError(true)).finally(() => setLoading(false));
  };

  useEffect(load, [reviewId]);

  const handleDecision = (d: 'CONFIRM' | 'CORRECT' | 'REJECT') => {
    setSubmitting(true);
    reviewService.resolve(reviewId!, d, editValue).then(() => {
      setDecision(d);
      setSubmitting(false);
    });
  };

  if (loading) {
    return (
      <div>
        <Skeleton className="h-4 w-32 mb-4" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (error || !item) {
    return (
      <div>
        <Link to="/review" className="inline-flex items-center gap-1.5 text-sm text-brand-600 hover:text-brand-700 mb-4">
          <ArrowLeft className="h-4 w-4" /> Back to Review Queue
        </Link>
        <ErrorState title="Unable to load review item" onRetry={load} />
      </div>
    );
  }

  return (
    <div>
      <Link to="/review" className="inline-flex items-center gap-1.5 text-sm text-brand-600 hover:text-brand-700 mb-4">
        <ArrowLeft className="h-4 w-4" /> Back to Review Queue
      </Link>

      <PageHeader
        title={`Review ${item.caseId}`}
        subtitle={`${item.checkpointName} · ${item.issue}`}
        actions={
          <Link to={`/cases/${item.caseId}`} className="btn-secondary text-xs">
            View full case
          </Link>
        }
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Document pane */}
        <div className="card overflow-hidden">
          <div className="px-4 py-3 border-b border-ink-200">
            <h3 className="text-sm font-semibold text-ink-800 flex items-center gap-2">
              <FileText className="h-4 w-4 text-ink-400" /> Document
            </h3>
          </div>
          <div className="bg-ink-100 flex items-center justify-center min-h-[400px] p-6">
            {doc ? (
              <div className="bg-white shadow-pop rounded-sm w-full max-w-sm aspect-[3/4] flex flex-col items-center justify-center p-8 text-center">
                <FileText className="h-12 w-12 text-ink-300 mb-2" />
                <p className="text-sm font-medium text-ink-400">{doc.name}</p>
                <p className="text-xs text-ink-300 mt-1">{doc.type} · {doc.pages} pages</p>
                <p className="text-[10px] text-ink-300 mt-4 max-w-[200px]">
                  Document preview will render here when backend provides page images.
                </p>
              </div>
            ) : (
              <div className="text-center text-ink-400">
                <AlertTriangle className="h-10 w-10 mx-auto mb-2" />
                <p className="text-sm">Document not available</p>
                <p className="text-xs text-ink-400 mt-1">This may be a missing-document review item.</p>
              </div>
            )}
          </div>
        </div>

        {/* Extracted field + actions pane */}
        <div className="space-y-4">
          <div className="card p-5">
            <h3 className="text-sm font-semibold text-ink-800 mb-3">Extracted Field</h3>
            <dl className="space-y-3">
              <div>
                <dt className="text-xs text-ink-500">Field</dt>
                <dd className="text-sm font-medium text-ink-800 mt-0.5">{item.fieldName}</dd>
              </div>
              <div>
                <dt className="text-xs text-ink-500">Extracted Value</dt>
                <dd className="text-base font-semibold text-ink-900 mt-0.5 tabular-nums">
                  {editing ? (
                    <input
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      className="input mt-1"
                      autoFocus
                    />
                  ) : (
                    item.extractedValue
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-ink-500">Confidence</dt>
                <dd className="mt-1">
                  {item.confidence > 0 ? (
                    <div className="flex items-center gap-2">
                      <div className="flex-1 max-w-[180px]"><ConfidenceBar value={item.confidence} /></div>
                      <span className="font-mono text-xs text-ink-600">{item.confidence.toFixed(1)}%</span>
                    </div>
                  ) : (
                    <span className="text-ink-400 text-xs">No confidence data</span>
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-ink-500">System Recommendation</dt>
                <dd className="mt-0.5">
                  <span className={`chip ring-1 ring-inset ${item.systemRecommendation === 'DISCREPANCY' ? 'bg-discrepancy-50 text-discrepancy-700 ring-discrepancy-500/20' : 'bg-review-50 text-review-700 ring-review-500/20'}`}>
                    {item.systemRecommendation}
                  </span>
                </dd>
              </div>
            </dl>

            {!editing && !decision && (
              <button onClick={() => { setEditing(true); setEditValue(item.extractedValue); }} className="btn-secondary mt-4 w-full">
                <Edit3 className="h-4 w-4" /> Edit Value
              </button>
            )}
            {editing && (
              <div className="flex gap-2 mt-4">
                <button onClick={() => setEditing(false)} className="btn-secondary flex-1">Cancel</button>
                <button onClick={() => setEditing(false)} className="btn-primary flex-1">Save edit</button>
              </div>
            )}
          </div>

          <div className="card p-5">
            <h3 className="text-sm font-semibold text-ink-800 mb-1">Operator Decision</h3>
            <p className="text-xs text-ink-500 mb-4">Every decision is recorded in the audit log.</p>
            {decision ? (
              <div className="rounded-md bg-verified-50 border border-verified-500/20 p-4 flex items-center gap-2 text-sm text-verified-700">
                <Check className="h-5 w-5" />
                Decision <span className="font-semibold">{decision}</span> recorded. This action is auditable.
                <button onClick={() => navigate('/review')} className="btn-ghost ml-auto text-xs">Back to queue</button>
              </div>
            ) : (
              <div className="grid grid-cols-3 gap-2">
                <button onClick={() => handleDecision('CONFIRM')} disabled={submitting} className="btn-primary">
                  <Check className="h-4 w-4" /> Confirm
                </button>
                <button onClick={() => handleDecision('CORRECT')} disabled={submitting} className="btn-secondary">
                  <Edit3 className="h-4 w-4" /> Correct
                </button>
                <button onClick={() => handleDecision('REJECT')} disabled={submitting} className="btn-danger">
                  <X className="h-4 w-4" /> Reject
                </button>
              </div>
            )}
          </div>

          <div className="card p-4 flex items-center gap-2 text-xs text-ink-500">
            <History className="h-4 w-4 text-ink-400" />
            All operator actions are written to the audit log with timestamp and user identity.
          </div>
        </div>
      </div>
    </div>
  );
}
