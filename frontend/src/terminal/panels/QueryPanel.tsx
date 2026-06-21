import { useState } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import { chatService, type QueryResponse } from '../../services/api';
import { useCockpit } from '../CockpitProvider';

export default function QueryPanel(_props: IDockviewPanelProps) {
  const { activeDataset } = useCockpit();
  const [prompt, setPrompt] = useState('');
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    if (!prompt.trim()) return;
    setBusy(true); setError(null);
    try {
      const res = await chatService.sendMessage(
        prompt,
        activeDataset ? { uploadedFile: activeDataset } : undefined,
      );
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Query failed');
    } finally {
      setBusy(false);
    }
  };

  const er = result?.execution_result;
  return (
    <div data-testid="query-panel" className="aura-panel query-panel">
      {activeDataset && <div className="panel-context">dataset: {activeDataset}</div>}
      <div className="query-bar">
        <input data-testid="query-input" value={prompt}
               onChange={(e) => setPrompt(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') run(); }}
               placeholder="Ask a question…" />
        <button data-testid="query-run" onClick={run} disabled={busy}>{busy ? '…' : 'Run'}</button>
      </div>
      {error && <div className="panel-error-inline">{error}</div>}
      {result?.final_query && <pre className="query-sql">{result.final_query}</pre>}
      {er?.columns && er.rows && (
        <table className="query-table">
          <thead><tr>{er.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {er.rows.slice(0, 100).map((row, i) => (
              <tr key={i}>{row.map((v, j) => <td key={j}>{String(v)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
