import { useEffect, useState } from 'react';
import { lineageService, type LineageGraph } from '../../services/api';

// Lineage rail: graph summary + guidance. (The page hides its inline inspector
// when the rail is present; node-level inspection lives on the canvas.)
export default function LineageRail() {
  const [g, setG] = useState<LineageGraph | null>(null);

  useEffect(() => {
    lineageService.get().then(setG).catch(() => {});
  }, []);

  if (!g) return <p className="rail-empty">Loading lineage…</p>;

  const s = g.summary;
  return (
    <div className="rail-section">
      <h4 className="rail-section__title">Graph</h4>
      <ul className="rail-stat">
        <li><b>{s.tables}</b> tables</li>
        <li><b>{s.queries}</b> queries</li>
        <li><b>{s.dashboards}</b> dashboards</li>
        <li><b>{s.edges}</b> edges</li>
      </ul>
      <p className="rail-hint">Select a node on the graph to inspect it.</p>
    </div>
  );
}
