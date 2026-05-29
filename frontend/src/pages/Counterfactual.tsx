import React, { useCallback, useMemo, useState } from 'react';
import CounterfactualCard, {
  type CounterfactualOperatorView,
} from '../components/CounterfactualCard';
import { API_BASE_URL } from '../services/api';

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


const Counterfactual: React.FC = () => {
  const [audience, setAudience] = useState<Audience>('operator');
  const [queryText, setQueryText] = useState(
    JSON.stringify({ ...BASE_QUERY, audience: 'operator' }, null, 2),
  );
  const [artifact, setArtifact] = useState<CounterfactualOperatorView | null>(null);
  const [recordHash, setRecordHash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<string>('');

  // Re-emit the textarea body when the user picks a new audience tier
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
          setRecordHash(status.artifact.audit_record_hash ?? null);
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
    <div style={{ padding: 24, maxWidth: 980, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 style={{ margin: 0 }}>Counterfactual Audit</h2>
        <p style={{ margin: '4px 0 0', color: 'var(--text-secondary, #94a3b8)', fontSize: 13 }}>
          Ask a counterfactual question. The engine returns a causally-grounded estimate
          with adversarial review and a hash-sealed audit reference. Each artifact runs
          four estimators and four refutation tests; the confidence badge reflects how
          much they agreed.
        </p>
      </div>

      <fieldset
        style={{
          border: '1px solid var(--border, #1e293b)',
          borderRadius: 6,
          padding: '8px 12px 12px',
          margin: 0,
        }}
      >
        <legend style={{ fontSize: 12, color: 'var(--text-secondary, #94a3b8)', padding: '0 6px' }}>
          Audience
        </legend>
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          {(['operator', 'auditor', 'analyst'] as const).map(a => (
            <label
              key={a}
              data-testid={`audience-${a}`}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 13,
                color: 'var(--text-primary, #f1f5f9)',
                cursor: 'pointer',
              }}
            >
              <input
                type="radio"
                name="audience"
                value={a}
                checked={audience === a}
                onChange={() => onAudienceChange(a)}
              />
              <div>
                <div style={{ fontWeight: 500 }}>{a}</div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary, #94a3b8)' }}>
                  {AUDIENCE_BLURB[a]}
                </div>
              </div>
            </label>
          ))}
        </div>
      </fieldset>

      <textarea
        data-testid="counterfactual-query-input"
        value={queryText}
        onChange={e => setQueryText(e.target.value)}
        spellCheck={false}
        style={{
          width: '100%',
          height: 280,
          fontFamily: 'monospace',
          fontSize: 12,
          background: 'var(--card-bg, #0f172a)',
          color: 'var(--text-primary, #f1f5f9)',
          border: '1px solid var(--border, #1e293b)',
          borderRadius: 6,
          padding: 12,
          resize: 'vertical',
        }}
      />

      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button
          type="button"
          onClick={submit}
          disabled={running}
          style={{
            padding: '8px 16px',
            borderRadius: 4,
            border: '1px solid var(--accent, #0284c7)',
            background: running ? 'var(--bg-muted, #1e293b)' : 'var(--accent, #0284c7)',
            color: 'white',
            cursor: running ? 'not-allowed' : 'pointer',
            opacity: running ? 0.7 : 1,
          }}
        >
          {running ? 'Running…' : 'Run counterfactual'}
        </button>
        {progress && (
          <span style={{ fontSize: 12, color: 'var(--text-secondary, #94a3b8)' }}>
            {progress}
          </span>
        )}
      </div>

      {error && (
        <pre
          style={{
            color: '#fca5a5',
            fontSize: 12,
            whiteSpace: 'pre-wrap',
            background: 'rgba(127, 29, 29, 0.2)',
            padding: 12,
            borderRadius: 4,
            border: '1px solid #7f1d1d',
            margin: 0,
          }}
        >
          {error}
        </pre>
      )}

      {artifact && <CounterfactualCard artifact={artifact} />}

      {recordHash && (
        <div
          data-testid="auditor-actions"
          style={{
            display: 'flex',
            gap: 12,
            flexWrap: 'wrap',
            fontSize: 13,
            padding: 12,
            border: '1px solid var(--border, #1e293b)',
            borderRadius: 6,
            background: 'var(--card-bg, rgba(15, 23, 42, 0.5))',
          }}
        >
          {pdfUrl && (
            <a
              href={pdfUrl}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="download-pdf"
              style={{ color: 'var(--accent, #38bdf8)', textDecoration: 'underline' }}
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
              style={{ color: 'var(--accent, #38bdf8)', textDecoration: 'underline' }}
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
              style={{ color: 'var(--accent, #38bdf8)', textDecoration: 'underline' }}
            >
              ✓ Verify ED25519 signature
            </a>
          )}
        </div>
      )}
    </div>
  );
};

export default Counterfactual;
