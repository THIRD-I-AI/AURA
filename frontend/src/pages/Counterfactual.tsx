/* Counterfactual Audit — native shadcn/ui + Tailwind (frontend/CLAUDE.md):
   ui-kit primitives + token utilities, no inline styles. Submits a causal
   counterfactual job, polls to completion, renders the operator card + the
   signed-artifact actions. Mounts in the workbench (viewRegistry
   'Counterfactuals') and, until it's deleted, the classic App shell. */
import { useCallback, useMemo, useState } from 'react';

import { Button } from '@/components/ui-kit/button';
import CounterfactualCard, {
  type CounterfactualOperatorView,
} from '../components/CounterfactualCard';
import { API_BASE_URL, sanitizeRecordHash } from '../services/api';

type Audience = 'operator' | 'auditor' | 'analyst';

const BASE_QUERY = {
  question: "What would Q3 revenue have been if we hadn't raised prices in May?",
  treatment: { column: 'price_change_may', actual: 0.08, counterfactual: 0.0 },
  outcome:   { column: 'monthly_revenue', agg: 'sum', window: ['2025-07-01', '2025-09-30'] },
  dag: {
    edges: [
      ['seasonality', 'monthly_revenue'],
      ['price_change_may', 'monthly_revenue'],
      ['seasonality', 'price_change_may'],
    ],
  },
  dataset: { source_id: 'uploaded_file:sales_2025.csv' },
};

const AUDIENCE_BLURB: Record<Audience, string> = {
  operator: 'Chat card with confidence badge + top challenges. Default for everyday use.',
  auditor:  'Full estimator + refutation tables + every challenge + signed PDF download.',
  analyst:  'Operator view + raw artifact JSON for programmatic drill-down.',
};

export default function Counterfactual() {
  const [audience, setAudience] = useState<Audience>('operator');
  const [queryText, setQueryText] = useState(
    JSON.stringify({ ...BASE_QUERY, audience: 'operator' }, null, 2),
  );
  const [artifact, setArtifact] = useState<CounterfactualOperatorView | null>(null);
  const [recordHash, setRecordHash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<string>('');

  // Re-emit the textarea body when the user picks a new audience tier.
  const onAudienceChange = useCallback((next: Audience) => {
    setAudience(next);
    try {
      const parsed = JSON.parse(queryText);
      parsed.audience = next;
      setQueryText(JSON.stringify(parsed, null, 2));
    } catch {
      // textarea has user edits that didn't parse; leave it alone, just
      // remember the radio selection.
    }
  }, [queryText]);

  const submit = useCallback(async () => {
    setRunning(true);
    setError(null);
    setArtifact(null);
    setRecordHash(null);
    setProgress('Submitting…');

    try {
      const submitResp = await fetch(`${API_BASE_URL}/counterfactual/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: queryText,
      });
      if (!submitResp.ok) {
        throw new Error(`HTTP ${submitResp.status}: ${await submitResp.text()}`);
      }
      const { job_id } = await submitResp.json();
      setProgress(`Job ${job_id} submitted, awaiting estimators…`);

      for (let i = 0; i < 120; i++) {
        await new Promise(res => setTimeout(res, 1000));
        const statusResp = await fetch(`${API_BASE_URL}/counterfactual/jobs/${job_id}`);
        const status = await statusResp.json();
        if (status.state === 'succeeded') {
          setArtifact(status.artifact.rendered as CounterfactualOperatorView);
          setRecordHash(sanitizeRecordHash(status.artifact.audit_record_hash));
          setProgress('Completed.');
          return;
        }
        if (status.state === 'failed') {
          throw new Error(status.error || 'Job failed');
        }
        setProgress(`Job ${job_id}: ${status.state} (${i + 1}s)`);
      }
      throw new Error('Job did not complete within 2 minutes.');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setProgress('');
    } finally {
      setRunning(false);
    }
  }, [queryText]);

  const pdfUrl = useMemo(
    () => recordHash ? `${API_BASE_URL}/counterfactual/artifacts/${recordHash}/report.pdf` : null,
    [recordHash],
  );
  const replayUrl = useMemo(
    () => recordHash ? `${API_BASE_URL}/counterfactual/artifacts/${recordHash}` : null,
    [recordHash],
  );
  const verifyUrl = useMemo(
    () => recordHash ? `${API_BASE_URL}/counterfactual/artifacts/${recordHash}/verify` : null,
    [recordHash],
  );

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-counterfactual-panel">
      <div>
        <h2 className="font-display text-lg font-semibold text-card-foreground">Counterfactual Audit</h2>
        <p className="mt-1 max-w-2xl text-xs leading-snug text-text-tertiary">
          Ask a counterfactual question. The engine returns a causally-grounded estimate with
          adversarial review and a hash-sealed audit reference. Each artifact runs four estimators
          and four refutation tests; the confidence badge reflects how much they agreed.
        </p>
      </div>

      <fieldset className="rounded-none border border-border p-3">
        <legend className="px-1.5 font-mono text-2xs uppercase tracking-widest text-text-tertiary">
          Audience
        </legend>
        <div className="flex flex-wrap gap-6">
          {(['operator', 'auditor', 'analyst'] as const).map(a => (
            <label
              key={a}
              data-testid={`audience-${a}`}
              className="flex cursor-pointer items-start gap-2 text-sm text-card-foreground"
            >
              <input
                type="radio"
                name="audience"
                value={a}
                checked={audience === a}
                onChange={() => onAudienceChange(a)}
                className="mt-0.5 accent-[var(--signal)]"
              />
              <span>
                <span className="block font-medium capitalize">{a}</span>
                <span className="block text-2xs text-text-tertiary">{AUDIENCE_BLURB[a]}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <textarea
        data-testid="counterfactual-query-input"
        value={queryText}
        onChange={e => setQueryText(e.target.value)}
        spellCheck={false}
        aria-label="Counterfactual query (JSON)"
        className="h-72 w-full resize-y rounded-none border border-border bg-card p-3 font-mono text-xs text-card-foreground focus:border-signal focus:outline-none"
      />

      <div className="flex items-center gap-3">
        <Button type="button" onClick={submit} disabled={running}>
          {running ? 'Running…' : 'Run counterfactual'}
        </Button>
        {progress && <span className="font-mono text-2xs text-text-tertiary">{progress}</span>}
      </div>

      {error && (
        <pre className="overflow-x-auto whitespace-pre-wrap rounded-none border border-danger bg-secondary p-3 font-mono text-xs text-danger">
          {error}
        </pre>
      )}

      {artifact && <CounterfactualCard artifact={artifact} />}

      {recordHash && (
        <div
          data-testid="auditor-actions"
          className="flex flex-wrap gap-4 rounded-none border border-border bg-card p-3 text-xs"
        >
          {pdfUrl && (
            <a
              href={pdfUrl}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="download-pdf"
              className="text-info underline underline-offset-2 hover:text-signal"
            >
              ↓ Download PDF report
            </a>
          )}
          {replayUrl && (
            <a
              href={replayUrl}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="open-replay-json"
              className="text-info underline underline-offset-2 hover:text-signal"
            >
              ⤴ Open persisted artifact (JSON)
            </a>
          )}
          {verifyUrl && (
            <a
              href={verifyUrl}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="verify-signature"
              className="text-info underline underline-offset-2 hover:text-signal"
            >
              ✓ Verify ED25519 signature
            </a>
          )}
        </div>
      )}
    </div>
  );
}
