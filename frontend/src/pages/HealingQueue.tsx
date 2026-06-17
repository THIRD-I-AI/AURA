/**
 * Healing Queue — S41 supervised self-healing.
 *
 * Lists drift recoveries that AURA generated + sandbox-validated but held
 * (PENDING_APPROVAL) under the risk-tiered policy, and lets a human approve
 * (deploy the shim) or reject (escalate). Mirrors the Exception Queue pattern.
 */
import { useCallback, useEffect, useState } from 'react';
import { healingService, type PendingRecovery } from '../services/api';
import { useAuth } from '../auth/AuthContext';

const card: React.CSSProperties = {
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-default)',
  borderRadius: 'var(--radius-lg)',
  padding: 'var(--space-4) var(--space-5)',
  display: 'flex',
  flexDirection: 'column',
  gap: 'var(--space-3)',
};

const label: React.CSSProperties = {
  fontSize: '10px',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.07em',
  color: 'var(--text-tertiary)',
};

function methodBadge(method: string): React.CSSProperties {
  const safe = method === 'template';
  return {
    fontSize: 10,
    fontWeight: 700,
    padding: '2px 8px',
    borderRadius: 'var(--radius-full)',
    whiteSpace: 'nowrap',
    background: safe ? 'var(--green-dim)' : 'var(--yellow-dim)',
    color: safe ? 'var(--fg-green)' : 'var(--fg-yellow)',
    border: `1px solid ${safe ? 'var(--green-border)' : 'var(--yellow-border)'}`,
  };
}

const btn = (kind: 'approve' | 'reject'): React.CSSProperties => ({
  padding: 'var(--space-2) var(--space-4)',
  fontSize: 'var(--font-sm)',
  fontWeight: 600,
  borderRadius: 'var(--radius-md)',
  cursor: 'pointer',
  fontFamily: 'var(--font-sans)',
  border: '1px solid',
  background: kind === 'approve' ? 'var(--green-dim)' : 'var(--red-dim)',
  borderColor: kind === 'approve' ? 'var(--green-border)' : 'var(--red-border)',
  color: kind === 'approve' ? 'var(--fg-green)' : 'var(--fg-red)',
});

export default function HealingQueue() {
  const { user } = useAuth();
  const approver = user?.email ?? user?.name ?? user?.sub ?? 'operator';

  const [items, setItems] = useState<PendingRecovery[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await healingService.pending());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load the healing queue');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const decide = async (id: string, action: 'approve' | 'reject') => {
    const note = notes[id]?.trim() ?? '';
    if (action === 'reject' && !note) {
      setError('A reason is required to reject a fix.');
      return;
    }
    setBusy(id);
    setError(null);
    try {
      if (action === 'approve') await healingService.approve(id, approver, note || undefined);
      else await healingService.reject(id, approver, note);
      setItems((prev) => prev.filter((r) => r.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : `Failed to ${action} the fix`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', height: '100%', minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
        <p style={{ margin: 0, fontSize: 'var(--font-sm)', color: 'var(--text-secondary)' }}>
          Fixes AURA generated and validated, held for your decision. Approving deploys the shim; rejecting escalates it.
        </p>
        <button
          onClick={load}
          style={{ ...btn('approve'), background: 'transparent', borderColor: 'var(--border-default)', color: 'var(--text-secondary)' }}
        >
          ↻ Refresh
        </button>
      </div>

      {error && (
        <div role="alert" style={{ padding: 'var(--space-3)', background: 'var(--red-dim)', border: '1px solid var(--red-border)', borderRadius: 'var(--radius-md)', color: 'var(--fg-red)', fontSize: 'var(--font-sm)' }}>
          {error}
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
        {loading ? (
          <p style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-sm)' }}>Loading…</p>
        ) : items.length === 0 ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 'var(--space-2)', color: 'var(--text-tertiary)', textAlign: 'center', padding: 'var(--space-8)' }}>
            <div style={{ fontSize: 28 }}>✓</div>
            <p style={{ margin: 0, fontSize: 'var(--font-sm)', color: 'var(--text-secondary)', fontWeight: 500 }}>Nothing awaiting approval</p>
            <p style={{ margin: 0, fontSize: 'var(--font-xs)' }}>AURA is keeping data flowing. Risky fixes will appear here for your sign-off.</p>
          </div>
        ) : (
          items.map((r) => (
            <div key={r.id} style={card}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 'var(--font-sm)', fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>
                  {r.source_id ?? 'unknown source'}
                </span>
                <span style={methodBadge(r.generation_method)}>
                  {r.generation_method === 'template' ? 'template fix' : `${r.generation_method}-generated`}
                </span>
                {r.validation_passed && (
                  <span style={{ ...methodBadge('template'), background: 'var(--accent-dim)', color: 'var(--fg-accent)', borderColor: 'var(--accent-border)' }}>
                    sandbox-validated
                  </span>
                )}
                <span style={{ marginLeft: 'auto', fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>{r.id}</span>
              </div>

              {r.diagnosis && (
                <div>
                  <div style={label}>Diagnosis</div>
                  <p style={{ margin: '2px 0 0', fontSize: 'var(--font-sm)', color: 'var(--text-secondary)', lineHeight: 1.5 }}>{r.diagnosis}</p>
                </div>
              )}

              <div style={{ display: 'flex', gap: 'var(--space-5)', flexWrap: 'wrap', fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>
                <span>post-fix KL: <strong style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>{r.post_kl_divergence != null ? r.post_kl_divergence.toFixed(4) : '—'}</strong></span>
                <span>detected: <strong style={{ color: 'var(--text-secondary)' }}>{r.created_at ? new Date(r.created_at).toLocaleString() : '—'}</strong></span>
              </div>

              {r.shim_code && (
                <div>
                  <div style={label}>Proposed shim</div>
                  <pre style={{ margin: '4px 0 0', padding: 'var(--space-3)', background: 'var(--bg-sunken)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', fontSize: 11, color: 'var(--text-code)', fontFamily: 'var(--font-mono)', overflowX: 'auto', maxHeight: 220, whiteSpace: 'pre', lineHeight: 1.5 }}>
                    {r.shim_code}
                  </pre>
                </div>
              )}

              <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap', alignItems: 'center' }}>
                <input
                  value={notes[r.id] ?? ''}
                  onChange={(e) => setNotes((n) => ({ ...n, [r.id]: e.target.value }))}
                  placeholder="Note (required to reject)…"
                  style={{ flex: 1, minWidth: 200, height: 32, padding: '0 var(--space-3)', fontSize: 'var(--font-sm)', color: 'var(--text-primary)', background: 'var(--bg-sunken)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', fontFamily: 'var(--font-sans)' }}
                />
                <button disabled={busy === r.id} onClick={() => decide(r.id, 'approve')} style={{ ...btn('approve'), opacity: busy === r.id ? 0.5 : 1 }}>
                  Approve &amp; deploy
                </button>
                <button disabled={busy === r.id} onClick={() => decide(r.id, 'reject')} style={{ ...btn('reject'), opacity: busy === r.id ? 0.5 : 1 }}>
                  Reject
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
