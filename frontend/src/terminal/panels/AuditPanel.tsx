/**
 * AuditPanel — the Palantir-style financial-audit command deck.
 *
 * Gives the auditor terminal layout the same signature treatment the ops
 * layout gets from PipelinePanel: a risk-sorted triage rail of findings, an
 * honest cryptographic-verification banner, a per-finding inspector with a
 * human approve/reject control that writes a real HumanOverrideRecord, and a
 * running decisions log — all against the real financialAuditService. No
 * synthetic data: before an audit is run the deck is empty, and verification
 * stays 'unverified' until a real verify() call returns.
 */
import { useCallback, useMemo, useState } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import type { AuditFinding } from '../../services/api';
import { useAuditDeck } from '../audit/useAuditDeck';
import {
  RISK_GLYPH,
  VERIFY_GLYPH,
  riskLevelOf,
  sortFindingsByRisk,
  isDecidable,
  type RiskLevel,
} from '../audit/model';
import '../audit/audit.css';

const VERIFY_LABEL: Record<string, string> = {
  unverified: 'Unverified — run verify() to check the signature',
  verified: 'Verified — signature checked against the public key',
  broken: 'Broken — signature did not verify',
};

export default function AuditPanel(_props: IDockviewPanelProps) {
  const a = useAuditDeck();
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState<string>('');

  // Risk-sorted triage order is a pure function of the live findings.
  const sorted = useMemo(() => sortFindingsByRisk(a.findings), [a.findings]);

  const selectedFinding = useMemo<AuditFinding | null>(
    () => sorted.find((f) => f.finding_id === selected) ?? null,
    [sorted, selected],
  );

  const counts = useMemo(() => {
    const c: Record<RiskLevel, number> = {
      Critical: 0, High: 0, Medium: 0, Low: 0, Unknown: 0,
    };
    for (const f of sorted) c[riskLevelOf(f.risk_level)] += 1;
    return c;
  }, [sorted]);

  const decide = useCallback(
    async (finding: AuditFinding, approved: boolean) => {
      await a.decide(finding, approved, note.trim());
      setNote('');
      setSelected(null);
    },
    [a, note],
  );

  const findingCls = (f: AuditFinding) =>
    [
      'aud-finding',
      `aud-risk-${riskLevelOf(f.risk_level).toLowerCase()}`,
      selected === f.finding_id ? 'is-selected' : '',
    ].filter(Boolean).join(' ');

  return (
    <div data-testid="audit-panel" className="aura-panel aud-panel">
      {/* ── header / overall status ─────────────────────────────────── */}
      <div className="aud-head">
        <div className="aud-head-title">
          <span className="aud-head-glyph">§</span>
          <span>Financial Audit</span>
        </div>
        <div className={`aud-verify aud-verify-${a.verification}`}>
          <span className="aud-verify-glyph">{VERIFY_GLYPH[a.verification]}</span>
          <span className="aud-verify-label">{VERIFY_LABEL[a.verification]}</span>
        </div>
        <div className="aud-head-counts">
          {(['Critical', 'High', 'Medium', 'Low', 'Unknown'] as RiskLevel[])
            .filter((lvl) => counts[lvl] > 0)
            .map((lvl) => (
              <span key={lvl} className={`aud-count aud-risk-${lvl.toLowerCase()}`}>
                <span className="aud-count-glyph">{RISK_GLYPH[lvl]}</span>
                {counts[lvl]} {lvl}
              </span>
            ))}
        </div>
      </div>

      {/* ── control row ─────────────────────────────────────────────── */}
      <div className="aud-controls">
        <button
          className="aud-btn aud-btn-primary"
          disabled={a.busy}
          onClick={() => void a.runAudit()}
        >
          {a.busy ? 'Running…' : a.report ? 'Re-run audit' : 'Run audit'}
        </button>
        <button
          className="aud-btn"
          disabled={!a.report}
          onClick={() => void a.verify()}
        >
          Verify signature
        </button>
        {a.report && (
          <span className="aud-report-meta">
            <code className="aud-hash" title={a.report.record_hash}>
              {a.report.record_hash.slice(0, 16)}…
            </code>
            <span className="aud-sig">sig: {a.report.signature_status}</span>
            <span>{a.report.n_findings} findings</span>
          </span>
        )}
        {a.error && <span className="aud-error">{a.error}</span>}
      </div>

      {/* ── body: triage rail + inspector ───────────────────────────── */}
      <div className="aud-body">
        <div className="aud-rail">
          {sorted.length === 0 && (
            <div className="aud-empty">
              {a.report
                ? 'No findings in this report.'
                : 'Run an audit to populate the triage rail.'}
            </div>
          )}
          {sorted.map((f) => (
            <button
              key={f.finding_id}
              className={findingCls(f)}
              onClick={() => setSelected(f.finding_id)}
            >
              <span className="aud-finding-glyph">
                {RISK_GLYPH[riskLevelOf(f.risk_level)]}
              </span>
              <span className="aud-finding-body">
                <span className="aud-finding-top">
                  <span className="aud-finding-risk">{riskLevelOf(f.risk_level)}</span>
                  <span className="aud-finding-std">{f.pcaob_standard}</span>
                  {f.requires_human_review && (
                    <span className="aud-finding-review">review</span>
                  )}
                </span>
                <span className="aud-finding-desc">{f.description}</span>
              </span>
            </button>
          ))}
        </div>

        <div className="aud-inspector">
          {!selectedFinding && (
            <div className="aud-empty">Select a finding to inspect and decide.</div>
          )}
          {selectedFinding && (
            <>
              <div className="aud-insp-head">
                <span className={`aud-insp-risk aud-risk-${riskLevelOf(selectedFinding.risk_level).toLowerCase()}`}>
                  {RISK_GLYPH[riskLevelOf(selectedFinding.risk_level)]}{' '}
                  {riskLevelOf(selectedFinding.risk_level)}
                </span>
                <code className="aud-insp-id">{selectedFinding.finding_id}</code>
                <span className="aud-insp-std">{selectedFinding.pcaob_standard}</span>
              </div>
              <p className="aud-insp-desc">{selectedFinding.description}</p>
              <div className="aud-insp-evidence">
                <div className="aud-insp-label">Evidence</div>
                <pre className="aud-evidence-pre">
                  {JSON.stringify(selectedFinding.evidence_payload, null, 2)}
                </pre>
              </div>
              {isDecidable(selectedFinding) ? (
                <div className="aud-decide">
                  <textarea
                    className="aud-note"
                    placeholder="Rationale (attached to the override record)…"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                  <div className="aud-decide-btns">
                    <button
                      className="aud-btn aud-btn-approve"
                      disabled={a.busy}
                      onClick={() => void decide(selectedFinding, true)}
                    >
                      Approve
                    </button>
                    <button
                      className="aud-btn aud-btn-reject"
                      disabled={a.busy}
                      onClick={() => void decide(selectedFinding, false)}
                    >
                      Reject
                    </button>
                  </div>
                </div>
              ) : (
                <div className="aud-insp-noreview">
                  This finding does not require human review.
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── decisions log ───────────────────────────────────────────── */}
      {a.decisions.length > 0 && (
        <div className="aud-decisions">
          <div className="aud-decisions-head">Decisions</div>
          {a.decisions.map((d) => (
            <div key={`${d.finding_id}-${d.at}`} className="aud-decision">
              <span className={`aud-decision-mark ${d.approved ? 'is-approve' : 'is-reject'}`}>
                {d.approved ? '\u2714' : '\u2718'}
              </span>
              <code className="aud-decision-id">{d.finding_id}</code>
              <span className="aud-decision-who">{d.auditor_id}</span>
              <span className="aud-decision-at">{new Date(d.at).toLocaleTimeString()}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
