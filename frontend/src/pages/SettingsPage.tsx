import { useEffect, useState } from 'react';
import { Save, Check } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Skeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/ui/ErrorState';
import { settingsService } from '@/services';
import type { SystemSettings } from '@/types';

export default function SettingsPage() {
  const [s, setS] = useState<SystemSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = () => {
    setLoading(true);
    setError(false);
    settingsService.get().then(setS).catch(() => setError(true)).finally(() => setLoading(false));
  };

  useEffect(load, []);

  const handleSave = () => {
    if (!s) return;
    setSaving(true);
    setSaved(false);
    settingsService.save(s).then(() => { setSaving(false); setSaved(true); setTimeout(() => setSaved(false), 2500); });
  };

  if (loading) {
    return (
      <div>
        <PageHeader title="Settings" />
        <Skeleton className="h-96 w-full max-w-2xl" />
      </div>
    );
  }

  if (error || !s) {
    return (
      <div>
        <PageHeader title="Settings" />
        <ErrorState title="Unable to load settings" onRetry={load} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Settings"
        subtitle="Configure processing thresholds and system behavior."
        actions={
          <button onClick={handleSave} disabled={saving} className="btn-primary">
            {saved ? <><Check className="h-4 w-4" /> Saved</> : <><Save className="h-4 w-4" /> {saving ? 'Saving…' : 'Save changes'}</>}
          </button>
        }
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 max-w-4xl">
        {/* Processing */}
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-800 mb-4">Processing Thresholds</h3>
          <div className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-ink-700 mb-1">OCR Confidence Threshold</label>
              <p className="text-xs text-ink-500 mb-2">Below this, OCR results are flagged for review.</p>
              <div className="flex items-center gap-3">
                <input
                  type="range" min={0} max={100} value={s.ocrConfidenceThreshold}
                  onChange={(e) => setS({ ...s, ocrConfidenceThreshold: Number(e.target.value) })}
                  className="flex-1 accent-brand-600"
                />
                <span className="font-mono text-sm text-ink-800 tabular-nums w-12 text-right">{s.ocrConfidenceThreshold}%</span>
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-ink-700 mb-1">VLM Fallback Threshold</label>
              <p className="text-xs text-ink-500 mb-2">Below this OCR confidence, VLM fallback is triggered.</p>
              <div className="flex items-center gap-3">
                <input
                  type="range" min={0} max={100} value={s.vlmFallbackThreshold}
                  onChange={(e) => setS({ ...s, vlmFallbackThreshold: Number(e.target.value) })}
                  className="flex-1 accent-brand-600"
                />
                <span className="font-mono text-sm text-ink-800 tabular-nums w-12 text-right">{s.vlmFallbackThreshold}%</span>
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-ink-700 mb-1">Human Review Threshold</label>
              <p className="text-xs text-ink-500 mb-2">Below this confidence, results are sent to human review.</p>
              <div className="flex items-center gap-3">
                <input
                  type="range" min={0} max={100} value={s.humanReviewThreshold}
                  onChange={(e) => setS({ ...s, humanReviewThreshold: Number(e.target.value) })}
                  className="flex-1 accent-brand-600"
                />
                <span className="font-mono text-sm text-ink-800 tabular-nums w-12 text-right">{s.humanReviewThreshold}%</span>
              </div>
            </div>
          </div>
        </div>

        {/* System */}
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-ink-800 mb-4">System</h3>
          <div className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-ink-700 mb-1">Processing Mode</label>
              <p className="text-xs text-ink-500 mb-2">Controls the level of automation in the pipeline.</p>
              <select
                value={s.processingMode}
                onChange={(e) => setS({ ...s, processingMode: e.target.value as SystemSettings['processingMode'] })}
                className="select"
              >
                <option value="AUTOMATED">Automated — no human intervention</option>
                <option value="ASSISTED">Assisted — humans review indeterminate cases</option>
                <option value="MANUAL">Manual — every case requires human approval</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-ink-700 mb-1">Session Timeout</label>
              <p className="text-xs text-ink-500 mb-2">Auto-logout after this many minutes of inactivity.</p>
              <input
                type="number" min={5} max={120} value={s.sessionTimeoutMinutes}
                onChange={(e) => setS({ ...s, sessionTimeoutMinutes: Number(e.target.value) })}
                className="input w-32"
              />
              <span className="text-sm text-ink-500 ml-2">minutes</span>
            </div>
            <div className="flex items-center justify-between pt-2">
              <div>
                <p className="text-sm font-medium text-ink-700">Notifications</p>
                <p className="text-xs text-ink-500">Email alerts for new review items.</p>
              </div>
              <button
                onClick={() => setS({ ...s, notificationsEnabled: !s.notificationsEnabled })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${s.notificationsEnabled ? 'bg-brand-600' : 'bg-ink-200'}`}
                role="switch"
                aria-checked={s.notificationsEnabled}
              >
                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${s.notificationsEnabled ? 'translate-x-6' : 'translate-x-1'}`} />
              </button>
            </div>
          </div>
        </div>

        <div className="card p-4 lg:col-span-2 bg-ink-50/50 border-ink-200">
          <p className="text-xs text-ink-500">
            API keys and backend credentials are managed server-side and are never exposed in the frontend.
            Contact your system administrator to configure DMS, NEO, OCR, or VLM integrations.
          </p>
        </div>
      </div>
    </div>
  );
}
