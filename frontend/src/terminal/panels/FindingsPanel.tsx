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

  return (
    <div data-testid="findings-panel" className="aura-panel findings-panel">
      <div className="findings-bar">
        <button data-testid="findings-run" onClick={run} disabled={busy}>
          {busy ? 'Running…' : 'Run sample audit'}
        </button>
        {activeDataset && <span className="panel-context">filtered: {activeDataset}</span>}
      </div>
      {error && <div className="panel-error-inline">{error}</div>}
      <ul className="findings-list">
        {shown.map((f) => (
          <li key={f.finding_id} className={`finding risk-${String(f.risk_level).toLowerCase()}`}>
            <span className="finding-std">{f.pcaob_standard}</span>
            <span className="finding-risk">{f.risk_level}</span>
            <span className="finding-desc">{f.description}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
