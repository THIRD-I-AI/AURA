import { useMemo, useState } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import { ArrowUpDown, ChevronUp, ChevronDown } from 'lucide-react';
import { financialAuditService, type AuditFinding } from '../../services/api';
import { SAMPLE_AUDIT_BATCH } from '../../audit/sampleAuditBatch';
import { useCockpit } from '../CockpitProvider';

type SortKey = 'standard' | 'risk';
type SortDir = 'asc' | 'desc';

// Severity order, not alphabetical — "critical" doesn't sort before "high" as a string.
const RISK_RANK: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1 };

export default function FindingsPanel(_props: IDockviewPanelProps) {
  const { activeDataset } = useCockpit();
  const [findings, setFindings] = useState<AuditFinding[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filterText, setFilterText] = useState('');
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');

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

  // activeDataset cross-filter from the Datasets panel — unchanged, composes
  // with the text filter below (both apply, AND logic).
  const datasetFiltered = useMemo(() => {
    if (!activeDataset) return findings;
    const needle = activeDataset.toLowerCase();
    return findings.filter((f) =>
      f.description.toLowerCase().includes(needle) ||
      JSON.stringify(f.evidence_payload).toLowerCase().includes(needle),
    );
  }, [findings, activeDataset]);

  const shown = useMemo(() => {
    const needle = filterText.trim().toLowerCase();
    const textFiltered = needle
      ? datasetFiltered.filter((f) =>
          f.description.toLowerCase().includes(needle) ||
          f.pcaob_standard.toLowerCase().includes(needle),
        )
      : datasetFiltered;
    if (!sortKey) return textFiltered;
    const dir = sortDir === 'asc' ? 1 : -1;
    return [...textFiltered].sort((a, b) => {
      if (sortKey === 'standard') return a.pcaob_standard.localeCompare(b.pcaob_standard) * dir;
      const ra = RISK_RANK[String(a.risk_level).toLowerCase()] ?? 0;
      const rb = RISK_RANK[String(b.risk_level).toLowerCase()] ?? 0;
      return (ra - rb) * dir;
    });
  }, [datasetFiltered, filterText, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  const sortIcon = (key: SortKey) => {
    if (sortKey !== key) return <ArrowUpDown size={14} className="sort-icon" aria-hidden />;
    return sortDir === 'asc'
      ? <ChevronUp size={14} className="sort-icon is-active" aria-hidden />
      : <ChevronDown size={14} className="sort-icon is-active" aria-hidden />;
  };

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
        <input
          className="findings-filter-input"
          data-testid="findings-filter"
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
          placeholder="Filter by standard or description…"
          aria-label="Filter findings"
        />
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
          <span className="panel-empty-hint">No findings match the current filters.</span>
        </div>
      ) : (
        <table className="findings-table">
          <thead>
            <tr>
              <th className="is-sortable" onClick={() => toggleSort('standard')}>
                <span className="th-inner">Standard{sortIcon('standard')}</span>
              </th>
              <th className="is-sortable" onClick={() => toggleSort('risk')}>
                <span className="th-inner">Risk{sortIcon('risk')}</span>
              </th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((f) => (
              <tr key={f.finding_id} className={`risk-${String(f.risk_level).toLowerCase()}`}>
                <td className="finding-std">{f.pcaob_standard}</td>
                <td className="finding-risk">{f.risk_level}</td>
                <td className="finding-desc" title={f.description}>{f.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
