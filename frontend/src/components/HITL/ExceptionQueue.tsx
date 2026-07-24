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
 *
 * Native shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit primitives + token
 * utilities, no inline styles.
 */
import { useCallback, useEffect, useState } from 'react';

import { Panel, PanelHeader, PanelBody } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { cn } from '@/lib/cn';
import {
  financialAuditService,
  type AuditFinding,
  type ExceptionQueueView,
  type FinancialAuditReport,
} from '../../services/api';
import { SAMPLE_AUDIT_BATCH } from '../../audit/sampleAuditBatch';

const RISK_TONE: Record<string, string> = {
  Critical: 'border-danger text-danger',
  High: 'border-warn text-warn',
  Medium: 'border-info text-info',
  Low: 'border-border text-text-tertiary',
};

function RiskBadge({ level }: { level: string }) {
  return (
    <span
      className={cn(
        'shrink-0 whitespace-nowrap border px-2 py-0.5 font-mono text-2xs font-semibold uppercase tracking-wider',
        RISK_TONE[level] ?? 'border-border text-text-secondary',
      )}
    >
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
      const r = await financialAuditService.runAudit(SAMPLE_AUDIT_BATCH);
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
    <div className="flex flex-col gap-5">
      <Panel>
        <PanelHeader title="PCAOB AS 1215 Exception Review" />
        <PanelBody className="flex flex-col gap-3">
          <p className="max-w-2xl text-xs leading-snug text-text-tertiary">
            Every AI finding below requires documented human judgment. Decisions are
            ED25519-signed and chained into the WORM audit log.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={runSampleAudit} disabled={busy}>
              {busy ? 'Working…' : 'Run sample financial audit'}
            </Button>
            {report && (
              <span className="text-sm text-text-secondary">
                Report <code className="font-mono">{report.record_hash.slice(0, 12)}…</code> · {report.signature_status} ·{' '}
                {verified === null ? 'verifying…' : verified ? '✓ signature verified' : '✗ VERIFICATION FAILED'}
              </span>
            )}
          </div>
          {error && (
            <p role="alert" className="border border-danger bg-secondary px-3 py-1.5 font-mono text-xs text-danger">
              {error}
            </p>
          )}
          {lastDecisionHash && (
            <p className="font-mono text-xs text-signal">
              Decision recorded as signed HumanOverrideRecord <code>{lastDecisionHash.slice(0, 12)}…</code>
            </p>
          )}
        </PanelBody>
      </Panel>

      {queue && (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
          <Panel>
            <PanelHeader title={`Pending exceptions (${queue.n_pending})`} />
            <PanelBody className="flex flex-col gap-3">
              <p className="font-mono text-2xs text-text-tertiary">
                {queue.n_decided} decided · PII shown as deterministic tokens or [REDACTED]
              </p>
              {queue.pending.length === 0 && (
                <p className="text-sm text-signal">
                  All exceptions cleared — the engagement file is complete.
                </p>
              )}
              <div className="flex flex-col gap-3">
                {queue.pending.map((f) => (
                  <button
                    key={f.finding_id}
                    onClick={() => setSelected(f)}
                    className={cn(
                      'flex flex-col gap-2 rounded-none border p-4 text-left transition-colors',
                      selected?.finding_id === f.finding_id
                        ? 'border-signal bg-secondary'
                        : 'border-border bg-card hover:bg-accent',
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <span className="font-mono text-sm font-semibold text-signal">{f.pcaob_standard}</span>
                      <RiskBadge level={f.risk_level} />
                    </div>
                    <p className="text-sm text-text-secondary">{f.description}</p>
                  </button>
                ))}
              </div>
            </PanelBody>
          </Panel>

          <Panel>
            <PanelHeader title="Review finding" />
            <PanelBody className="flex flex-col gap-3">
              <p className="font-mono text-2xs text-text-tertiary">
                AS 1215 contradiction record — rationale is mandatory
              </p>
              {!selected && <p className="text-sm text-text-tertiary">Select a pending exception to review.</p>}
              {selected && (
                <div className="flex flex-col gap-3">
                  <p className="text-sm text-card-foreground">{selected.description}</p>
                  <pre className="overflow-x-auto rounded-none border border-border bg-secondary p-3 font-mono text-2xs text-text-secondary">
                    {JSON.stringify(selected.evidence_payload, null, 2)}
                  </pre>
                  <label className="flex flex-col gap-2 text-sm text-text-secondary">
                    Auditor rationale
                    <textarea
                      value={rationale}
                      onChange={(e) => setRationale(e.target.value)}
                      rows={4}
                      placeholder="Explain why you are approving or overriding this AI finding…"
                      className="w-full resize-y rounded-none border border-border bg-card p-3 text-sm text-card-foreground focus:border-signal focus:outline-none"
                    />
                  </label>
                  <div className="flex gap-3">
                    <Button disabled={busy || !rationale.trim()} onClick={() => submitDecision(true)}>
                      Approve AI finding
                    </Button>
                    <Button variant="destructive" disabled={busy || !rationale.trim()} onClick={() => submitDecision(false)}>
                      Override AI finding
                    </Button>
                  </div>
                </div>
              )}
            </PanelBody>
          </Panel>
        </div>
      )}
    </div>
  );
}

export default ExceptionQueue;
