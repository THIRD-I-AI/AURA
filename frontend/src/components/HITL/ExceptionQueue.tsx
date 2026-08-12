/**
 * HITL Exception Queue — PCAOB AS 1215 review workbench (S35).
 *
 * Three independent ways into the queue below, kept visually separate so a
 * user can tell at a glance which one touches their own data: (1) load the
 * exception queue of an already-signed audit by record hash, (2) POST a
 * user-authored ledger for a real audit, (3) run the fixed walkthrough
 * sample. All three converge on the same flow: findings flagged
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
  sanitizeRecordHash,
  type AuditFinding,
  type ExceptionQueueView,
  type FinancialAuditReport,
} from '../../services/api';
import { SAMPLE_AUDIT_BATCH } from '../../audit/sampleAuditBatch';

// Shown as placeholder text (not a pre-filled value) so the empty textarea
// documents the accepted shape without looking like real fabricated rows.
// goods_receipts/historical_reports are arrays, so they belong here rather
// than as scalar form fields (period_end/subject_id/preparer_id below).
const OWN_LEDGER_PLACEHOLDER = `{
  "ledger": [{ "internal_id": "...", "account_code": "...", "amount": 0 }],
  "invoices": [{ "invoice_number": "...", "po_number": "...", "amount": 0 }],
  "purchase_orders": [{ "po_number": "..." }],
  "journal_entries": [{ "internal_id": "...", "amount": 0, "account_code": "..." }],
  "goods_receipts": [{ "po_number": "..." }],
  "historical_reports": [{ "account_code": "...", "amount": 0 }]
}`;

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
  const [hashInput, setHashInput] = useState('');
  const [hashError, setHashError] = useState<string | null>(null);
  const [ledgerText, setLedgerText] = useState('');
  const [ledgerError, setLedgerError] = useState<string | null>(null);
  // Scalar AS-1215/AS-2401 fields — kept as their own inputs (not JSON) since
  // a user would reasonably type single values here; left blank means "omit
  // the key" so the backend's documented default applies (see runOwnLedger).
  const [periodEnd, setPeriodEnd] = useState('');
  const [subjectId, setSubjectId] = useState('');
  const [preparerId, setPreparerId] = useState('');

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

  // Pre-validate client-side so an obviously malformed hash never reaches the
  // network — getExceptions/verify throw on this same 64-hex-char check, and
  // an unvalidated call would surface that as an unhandled rejection instead
  // of the inline message a typo deserves.
  const loadByHash = useCallback(async () => {
    const candidate = hashInput.trim();
    if (!sanitizeRecordHash(candidate)) {
      setHashError('Enter a 64-character sha256 hex record hash from a prior signed audit.');
      return;
    }
    setHashError(null);
    setError(null);
    setBusy(true);
    setSelected(null);
    setLastDecisionHash(null);
    setReport(null); // a hash lookup only yields the queue view, not a full report
    try {
      await financialAuditService.ensureAuditorToken();
      await refreshQueue(candidate);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [hashInput, refreshQueue]);

  const runOwnLedger = useCallback(async () => {
    let ledgerFields: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(ledgerText);
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Expected a JSON object with array fields, e.g. { "ledger": [...], "invoices": [...] }.');
      }
      ledgerFields = parsed as Record<string, unknown>;
    } catch (e) {
      setLedgerError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    setLedgerError(null);
    setError(null);
    setBusy(true);
    setSelected(null);
    setLastDecisionHash(null);
    try {
      await financialAuditService.ensureAuditorToken();
      // tenant_id is required on the wire, but the backend always overrides
      // it from the verified JWT (never trusts the body) — this value is
      // discarded server-side, so it isn't exposed as a user-facing field.
      const payload = { tenant_id: 'ignored-by-backend', ...ledgerFields } as
        Parameters<typeof financialAuditService.runAudit>[0];
      // Scalar fields come from their own inputs, not the JSON textarea.
      // Blank means "not provided" — omit the key entirely rather than
      // sending "" (which would overwrite the backend's documented default
      // with an empty value in a signed audit record).
      if (periodEnd.trim()) payload.period_end = periodEnd.trim();
      if (subjectId.trim()) payload.subject_id = subjectId.trim();
      if (preparerId.trim()) payload.preparer_id = preparerId.trim();
      const r = await financialAuditService.runAudit(payload);
      setReport(r);
      await refreshQueue(r.record_hash);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [ledgerText, periodEnd, subjectId, preparerId, refreshQueue]);

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
          {queue && (
            <span className="text-sm text-text-secondary">
              Record <code className="font-mono">{queue.record_hash.slice(0, 12)}…</code>
              {report && <> · {report.signature_status}</>} ·{' '}
              {verified === null ? 'verifying…' : verified ? '✓ signature verified' : '✗ VERIFICATION FAILED'}
            </span>
          )}
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

      <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
        <Panel>
          <PanelHeader title="Load a signed audit" />
          <PanelBody className="flex flex-col gap-3">
            <p className="text-xs leading-snug text-text-tertiary">
              Look up the exception queue of an audit that already ran, by its record hash.
            </p>
            <label className="flex flex-col gap-2 text-sm text-text-secondary">
              Record hash
              <input
                type="text"
                value={hashInput}
                onChange={(e) => { setHashInput(e.target.value); setHashError(null); }}
                placeholder="64-char sha256 hex, from a prior report"
                className="w-full rounded-none border border-border bg-card p-2 font-mono text-2xs text-card-foreground focus:border-signal focus:outline-none"
              />
            </label>
            {hashError && (
              <p role="alert" className="font-mono text-2xs text-danger">{hashError}</p>
            )}
            <Button variant="outline" onClick={loadByHash} disabled={busy || !hashInput.trim()}>
              {busy ? 'Working…' : 'Load audit'}
            </Button>
          </PanelBody>
        </Panel>

        <Panel>
          <PanelHeader title="Audit your ledger" />
          <PanelBody className="flex flex-col gap-3">
            <p className="text-xs leading-snug text-text-tertiary">
              Paste your own ledger/invoice/PO/journal-entry rows as JSON and run a real audit.
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <label className="flex flex-col gap-2 text-sm text-text-secondary">
                Period end (optional)
                <input
                  type="date"
                  value={periodEnd}
                  onChange={(e) => setPeriodEnd(e.target.value)}
                  className="w-full rounded-none border border-border bg-card p-2 font-mono text-2xs text-card-foreground focus:border-signal focus:outline-none"
                />
              </label>
              <label className="flex flex-col gap-2 text-sm text-text-secondary">
                Subject ID (optional)
                <input
                  type="text"
                  value={subjectId}
                  onChange={(e) => setSubjectId(e.target.value)}
                  placeholder="defaults to &quot;default&quot;"
                  className="w-full rounded-none border border-border bg-card p-2 font-mono text-2xs text-card-foreground focus:border-signal focus:outline-none"
                />
              </label>
              <label className="flex flex-col gap-2 text-sm text-text-secondary">
                Preparer ID (optional)
                <input
                  type="text"
                  value={preparerId}
                  onChange={(e) => setPreparerId(e.target.value)}
                  placeholder="defaults to &quot;system&quot;"
                  className="w-full rounded-none border border-border bg-card p-2 font-mono text-2xs text-card-foreground focus:border-signal focus:outline-none"
                />
              </label>
            </div>
            <label className="flex flex-col gap-2 text-sm text-text-secondary">
              Ledger JSON (goods receipts / historical reports go here as arrays)
              <textarea
                value={ledgerText}
                onChange={(e) => { setLedgerText(e.target.value); setLedgerError(null); }}
                rows={5}
                placeholder={OWN_LEDGER_PLACEHOLDER}
                className="w-full resize-y rounded-none border border-border bg-card p-2 font-mono text-2xs text-card-foreground focus:border-signal focus:outline-none"
              />
            </label>
            {ledgerError && (
              <p role="alert" className="font-mono text-2xs text-danger">{ledgerError}</p>
            )}
            <Button variant="outline" onClick={runOwnLedger} disabled={busy || !ledgerText.trim()}>
              {busy ? 'Working…' : 'Run my audit'}
            </Button>
          </PanelBody>
        </Panel>

        <Panel>
          <PanelHeader title="Demo scenario" />
          <PanelBody className="flex flex-col gap-3">
            <p className="text-xs leading-snug text-text-tertiary">
              Runs a fixed sample ledger for walkthroughs — not your data.
            </p>
            <Button onClick={runSampleAudit} disabled={busy}>
              {busy ? 'Working…' : 'Run demo scenario'}
            </Button>
          </PanelBody>
        </Panel>
      </div>

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
