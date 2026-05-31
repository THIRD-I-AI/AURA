import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { auditApi } from './auditApi';

export function AuditWizard() {
  const navigate = useNavigate();
  const [treatment, setTreatment] = useState('');
  const [outcome, setOutcome] = useState('');
  const [confounders, setConfounders] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = treatment.trim() !== '' && outcome.trim() !== '' && !busy;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const query = {
        treatment: treatment.trim(),
        outcome: outcome.trim(),
        confounders: confounders.split(',').map((c) => c.trim()).filter(Boolean),
      };
      const { job_id } = await auditApi.submitCustomAudit(query);
      navigate(`/audit/${job_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const field = (testid: string, label: string, value: string, setter: (v: string) => void, placeholder: string) => (
    <label style={{ display: 'block', marginBottom: 'var(--space-4)' }}>
      <span style={{ display: 'block', fontSize: 'var(--font-sm)', color: 'var(--text-secondary)', marginBottom: 'var(--space-1)' }}>{label}</span>
      <input data-testid={testid} value={value} placeholder={placeholder} onChange={(e) => setter(e.target.value)}
        style={{ width: '100%', padding: 'var(--space-3)', background: 'var(--bg-base)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)' }} />
    </label>
  );

  return (
    <div data-testid="audit-wizard" style={{ maxWidth: 560, margin: '0 auto' }}>
      <h2>Run a custom audit</h2>
      <p style={{ color: 'var(--text-tertiary)', marginBottom: 'var(--space-6)' }}>Define the causal question. We run the full estimator battery and seal a certificate.</p>
      {field('wizard-treatment', 'Treatment column', treatment, setTreatment, 'e.g. protected_class')}
      {field('wizard-outcome', 'Outcome column', outcome, setOutcome, 'e.g. approved')}
      {field('wizard-confounders', 'Confounders (comma-separated)', confounders, setConfounders, 'e.g. income, dti, credit_score')}
      {error && <p style={{ color: 'var(--red)' }}>{error}</p>}
      <button data-testid="wizard-submit" disabled={!canSubmit} onClick={submit}
        style={{ padding: 'var(--space-3) var(--space-6)', background: canSubmit ? 'var(--accent)' : 'var(--border-default)', color: '#fff', border: 'none', borderRadius: 'var(--radius-md)', cursor: canSubmit ? 'pointer' : 'not-allowed' }}>
        {busy ? 'Submitting…' : 'Run audit'}
      </button>
    </div>
  );
}
