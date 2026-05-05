import React, { useCallback, useState } from 'react';
import CounterfactualCard, {
  type CounterfactualOperatorView,
} from '../components/CounterfactualCard';

const SAMPLE_QUERY = {
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
  audience: 'operator',
};


const Counterfactual: React.FC = () => {
  const [queryText, setQueryText] = useState(JSON.stringify(SAMPLE_QUERY, null, 2));
  const [artifact, setArtifact] = useState<CounterfactualOperatorView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<string>('');

  const submit = useCallback(async () => {
    setRunning(true);
    setError(null);
    setArtifact(null);
    setProgress('Submitting…');

    try {
      const submitResp = await fetch('/api/v1/counterfactual/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: queryText,
      });
      if (!submitResp.ok) {
        throw new Error(`HTTP ${submitResp.status}: ${await submitResp.text()}`);
      }
      const { job_id } = await submitResp.json();
      setProgress(`Job ${job_id} submitted, awaiting estimators…`);

      // Poll. Engine takes ~10-30s on real data.
      for (let i = 0; i < 120; i++) {
        await new Promise(res => setTimeout(res, 1000));
        const statusResp = await fetch(`/api/v1/counterfactual/jobs/${job_id}`);
        const status = await statusResp.json();
        if (status.state === 'succeeded') {
          setArtifact(status.artifact.rendered as CounterfactualOperatorView);
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
    </div>
  );
};

export default Counterfactual;
