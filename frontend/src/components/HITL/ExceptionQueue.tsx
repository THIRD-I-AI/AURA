/**
 * HITL Exception Queue — PCAOB AS 1215 review workbench (S35).
 *
 * Live flow: run (or load) a signed financial audit → findings flagged
 * requires_human_review appear here → the auditor approves or overrides each
 * with a rationale → every decision becomes a signed HumanOverrideRecord +
 * WORM audit entry on the backend and the queue shrinks. Decisions need an
 * auditor/admin bearer token; in open auth mode we self-provision one. The
 * auditor's identity is never sent in the body — the backend binds it to the
 * verified JWT's `sub` claim (anti-impersonation, fail-closed).
 */
import { useCallback, useEffect, useState } from 'react';
import Card, { CardHeader, CardBody } from '../ui/Card';
import Button from '../ui/Button';
import {
  financialAuditService,
  type AuditFinding,
  type ExceptionQueueView,
  type FinancialAuditReport,
} from '../../services/api';

/** Canned ledger with one of each finding type — the investor demo batch.
 *  Unmatched PO (AS 2201), duplicate + round-dollar JEs (AS 2401), and a
 *  >$100k variance (AS 2305). PII here exercises the egress masking. */
const SAMPLE_BATCH = {
  tenant_id: 'demo-tenant',
  ledger: [{ internal_id: 'L-1001', account_code: '4000', amount: 250000.0 }],
  purchase_orders: [{ po_number: 'PO-7001' }],
  invoices: [
    { invoice_number: 'INV-9001', po_number: 'PO-MISSING', employee_name: 'Ada Lovelace', amount: 12400.5 },
    { invoice_number: 'INV-9002', po_number: 'PO-7001', employee_name: 'Grace Hopper', amount: 980.0 },
  ],
  journal_entries: [
    { internal_id: 'JE-1', amount: 5000.0, account_code: '6000', vendor_id: 'V-77' },
    { internal_id: 'JE-2', amount: 5000.0, account_code: '6000', vendor_id: 'V-77' },
  ],
};

const RISK_COLORS: Record<string, string> = {
  Critical: 'var(--error, #ef4444)',
  High: 'var(--warning, #f59e0b)',
  Medium: 'var(--accent, #6366f1)',
  Low: 'var(--text-tertiary, #9ca3af)',
};

function RiskBadge({ level }: { level: string }) {
  return (
    <span style={{
      fontSize: 'var(--font-xs, 12px)',
      fontWeight: 600,
      color: RISK_COLORS[level] || 'var(--text-secondary)',
      border: `1px solid ${RISK_COLORS[level] || 'var(--border-default)'}`,
      borderRadius: 'var(--radius-full, 999px)',
      padding: '2px 10px',
      whiteSpace: 'nowrap',
    }}>
      {level} risk
    </span>
  );
}

export function ExceptionQueue() {
  const [report, setReport] = useState<FinancialAuditReport | null>(null);
  const [queue, setQueue] = useState<ExceptionQueueView | null>(null);
  const [verified, setVerified] = useState<boolean | null>(null);
  const [selected, setSelected] = useState<AuditFinding | null>(null);
  const [rationale, setRationale] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastDecisionHash, setLastDecisionHash] = useState<string | null>(null);

  const refreshQueue = useCallback(async (recordHash: string) => {
    const [q, v] = await Promise.all([
      financialAuditService.getExceptions(recordHash),
      financialAuditService.verify(recordHash),
    ]);
    setQueue(q);
    setVerified(v.verified);
  }, []);

  const runSampleAudit = useCallback(async () => {
    setBusy(true);
    setError(null);
    setSelected(null);
    setLastDecisionHash(null);
    try {
      await financialAuditService.ensureAuditorToken();
      const r = await financialAuditService.runAudit(SAMPLE_BATCH);
      setReport(r);
      await refreshQueue(r.record_hash);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [refreshQueue]);

  const submitDecision = useCallback(async (approved: boolean) => {
    if (!queue || !selected || !rationale.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const d = await financialAuditService.decide(
        queue.record_hash, selected.finding_id, rationale.trim(), approved,
      );
      setLastDecisionHash(d.record_hash);
      setSelected(null);
      setRationale('');
      await refreshQueue(queue.record_hash);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [queue, selected, rationale, refreshQueue]);

  // Self-provision the auditor bearer up front so the first decision
  // doesn't pay the token round-trip (no-op when a token already exists).
  useEffect(() => {
    financialAuditService.ensureAuditorToken().catch(() => { /* surfaced on first action */ });
  }, []);

  return (
    <div style={{ display: 'grid', gap: 'var(--space-5, 20px)' }}>
      <Card>
        <CardHeader
          title="PCAOB AS 1215 Exception Review"
          subtitle="Every AI finding below requires documented human judgment. Decisions are ED25519-signed and chained into the WORM audit log."
        />
        <CardBody>
          <div style={{ display: 'flex', gap: 'var(--space-3, 12px)', alignItems: 'center', flexWrap: 'wrap' }}>
            <Button onClick={runSampleAudit} disabled={busy} variant="primary">
              {busy ? 'Working…' : 'Run sample financial audit'}
            </Button>
            {report && (
              <span style={{ fontSize: 'var(--font-sm, 13px)', color: 'var(--text-secondary)' }}>
                Report <code>{report.record_hash.slice(0, 12)}…</code> · {report.signature_status} ·{' '}
                {verified === null ? 'verifying…' : verified ? '✓ signature verified' : '✗ VERIFICATION FAILED'}
              </span>
            )}
          </div>
          {error && (
            <p role="alert" style={{ color: 'var(--error, #ef4444)', marginTop: 'var(--space-3, 12px)' }}>
              {error}
            </p>
          )}
          {lastDecisionHash && (
            <p style={{ color: 'var(--success, #22c55e)', fontSize: 'var(--font-sm, 13px)', marginTop: 'var(--space-2, 8px)' }}>
              Decision recorded as signed HumanOverrideRecord <code>{lastDecisionHash.slice(0, 12)}…</code>
            </p>
          )}
        </CardBody>
      </Card>

      {queue && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 'var(--space-5, 20px)' }}>
          <Card>
            <CardHeader
              title={`Pending exceptions (${queue.n_pending})`}
              subtitle={`${queue.n_decided} decided · PII shown as deterministic tokens or [REDACTED]`}
            />
            <CardBody>
              {queue.pending.length === 0 && (
                <p style={{ color: 'var(--success, #22c55e)' }}>
                  All exceptions cleared — the engagement file is complete.
                </p>
              )}
              <div style={{ display: 'grid', gap: 'var(--space-3, 12px)' }}>
                {queue.pending.map((f) => (
                  <button
                    key={f.finding_id}
                    onClick={() => setSelected(f)}
                    style={{
                      textAlign: 'left',
                      cursor: 'pointer',
                      background: selected?.finding_id === f.finding_id ? 'var(--bg-elevated, #1f2230)' : 'var(--bg-surface)',
                      border: `1px solid ${selected?.finding_id === f.finding_id ? 'var(--accent)' : 'var(--border-default)'}`,
                      borderRadius: 'var(--radius-lg, 12px)',
                      padding: 'var(--space-4, 16px)',
                      color: 'var(--text-primary)',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 'var(--space-3, 12px)' }}>
                      <strong style={{ color: 'var(--accent)' }}>{f.pcaob_standard}</strong>
                      <RiskBadge level={f.risk_level} />
                    </div>
                    <p style={{ margin: 'var(--space-2, 8px) 0 0', fontSize: 'var(--font-sm, 13px)' }}>{f.description}</p>
                  </button>
                ))}
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Review finding" subtitle="AS 1215 contradiction record — rationale is mandatory" />
            <CardBody>
              {!selected && <p style={{ color: 'var(--text-tertiary)' }}>Select a pending exception to review.</p>}
              {selected && (
                <div style={{ display: 'grid', gap: 'var(--space-3, 12px)' }}>
                  <p style={{ margin: 0 }}>{selected.description}</p>
                  <pre style={{
                    margin: 0,
                    fontSize: 'var(--font-xs, 12px)',
                    background: 'var(--bg-elevated, #1f2230)',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-md, 8px)',
                    padding: 'var(--space-3, 12px)',
                    overflowX: 'auto',
                  }}>
                    {JSON.stringify(selected.evidence_payload, null, 2)}
                  </pre>
                  <label style={{ fontSize: 'var(--font-sm, 13px)', color: 'var(--text-secondary)' }}>
                    Auditor rationale
                    <textarea
                      value={rationale}
                      onChange={(e) => setRationale(e.target.value)}
                      rows={4}
                      placeholder="Explain why you are approving or overriding this AI finding…"
                      style={{
                        display: 'block',
                        width: '100%',
                        marginTop: 'var(--space-2, 8px)',
                        background: 'var(--bg-surface)',
                        color: 'var(--text-primary)',
                        border: '1px solid var(--border-default)',
                        borderRadius: 'var(--radius-md, 8px)',
                        padding: 'var(--space-3, 12px)',
                        resize: 'vertical',
                      }}
                    />
                  </label>
                  <div style={{ display: 'flex', gap: 'var(--space-3, 12px)' }}>
                    <Button variant="success" disabled={busy || !rationale.trim()} onClick={() => submitDecision(true)}>
                      Approve AI finding
                    </Button>
                    <Button variant="danger" disabled={busy || !rationale.trim()} onClick={() => submitDecision(false)}>
                      Override AI finding
                    </Button>
                  </div>
                </div>
              )}
            </CardBody>
          </Card>
        </div>
      )}
    </div>
  );
}

export default ExceptionQueue;
