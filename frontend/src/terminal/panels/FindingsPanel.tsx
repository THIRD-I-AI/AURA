import { useMemo, useState } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import { financialAuditService, type AuditFinding } from '../../services/api';
import { SAMPLE_AUDIT_BATCH } from '../../audit/sampleAuditBatch';
import { useCockpit } from '../CockpitProvider';

export default function FindingsPanel(_props: IDockviewPanelProps) {
  const { activeDataset } = useCockpit();
  const [findings, setFindings] = useState<AuditFinding[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true); setError(null);
    try {
      await financialAuditService.ensureAuditorToken();
      const report = await financialAuditService.runAudit(SAMPLE_AUDIT_BATCH);
      setFindings(report.findings ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Audit failed');
    } finally {
      setBusy(false);
    }
  };

  const shown = useMemo(() => {
    if (!activeDataset) return findings;
    const needle = activeDataset.toLowerCase();
    return findings.filter((f) =>
      f.description.toLowerCase().includes(needle) ||
      JSON.stringify(f.evidence_payload).toLowerCase().includes(needle),
    );
  }, [findings, activeDataset]);

  const hasRun = findings.length > 0;
  return (
    <div data-testid="findings-panel" className="aura-panel findings-panel">
      <div className="panel-head">
        <span className="panel-head-glyph" aria-hidden>⚑</span>
        <span className="panel-head-title">Findings</span>
        <span className="panel-head-metric">
          {busy ? 'running…' : hasRun ? `${shown.length} shown` : 'idle'}
        </span>
      </div>
      <div className="findings-bar">
        <button data-testid="findings-run" onClick={run} disabled={busy}>
          {busy ? 'Running…' : 'Run sample audit'}
        </button>
        {activeDataset && <span className="panel-context">filtered: {activeDataset}</span>}
      </div>
      {error ? (
        <div className="panel-empty is-error" role="alert">
          <span className="panel-empty-glyph" aria-hidden>●</span>
          <span className="panel-empty-title">Audit failed</span>
          <span className="panel-empty-hint">{error}</span>
        </div>
      ) : busy ? (
        <div className="panel-empty" role="status">
          <span className="panel-empty-glyph" aria-hidden>◌</span>
          <span className="panel-empty-title">Running audit</span>
          <span className="panel-empty-hint">Verifying the sample batch against PCAOB standards…</span>
        </div>
      ) : !hasRun ? (
        <div className="panel-empty is-idle" role="status">
          <span className="panel-empty-glyph" aria-hidden>·</span>
          <span className="panel-empty-title">No audit run yet</span>
          <span className="panel-empty-hint">Run a sample audit to surface risk-ranked findings.</span>
        </div>
      ) : shown.length === 0 ? (
        <div className="panel-empty is-idle" role="status">
          <span className="panel-empty-glyph" aria-hidden>·</span>
          <span className="panel-empty-title">No matches</span>
          <span className="panel-empty-hint">No findings match the active dataset filter.</span>
        </div>
      ) : (
        <ul className="findings-list">
          {shown.map((f) => (
            <li key={f.finding_id} className={`finding risk-${String(f.risk_level).toLowerCase()}`}>
              <span className="finding-std">{f.pcaob_standard}</span>
              <span className="finding-risk">{f.risk_level}</span>
              <span className="finding-desc">{f.description}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
