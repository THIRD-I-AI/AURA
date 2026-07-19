/* Library — native terminal-authority panel (replaces embedded classic Library
   page). Lists real saved queries from GET /saved-queries via savedQueryService,
   styled to match the Cockpit. Read + star; full editing stays in the query
   flow that created them. */
import { useCallback, useEffect, useState } from 'react';
import { savedQueryService } from '../../services/api';

type SavedQuery = { id: string; name?: string; sql?: string; prompt?: string; starred?: boolean };

export default function LibraryPanel() {
  const [items, setItems] = useState<SavedQuery[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await savedQueryService.list();
      setItems((list ?? []) as SavedQuery[]);
      setError(null);
    } catch {
      setError('Could not reach the gateway to load the query library.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const count = items?.length ?? 0;
  const starred = (items ?? []).filter((q) => q.starred).length;

  return (
    <div data-testid="wb-library-panel" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="aw-mono" style={{ fontSize: 11, color: 'var(--text3)' }}>
          {items === null ? 'loading…' : `${count} saved quer${count === 1 ? 'y' : 'ies'}${starred ? ` · ${starred} starred` : ''}`}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={load} className="aw-mono aw-hover-accent-bd" style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, letterSpacing: '.04em', color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--border)', borderRadius: 0, padding: '7px 14px' }}>↻ REFRESH</button>
      </div>

      {error && <div className="aw-mono" style={{ fontSize: 11, color: 'var(--danger)', background: 'var(--sunken)', border: '1px solid var(--border)', padding: '6px 12px' }}>{error}</div>}

      <div className="aw-panel" style={{ overflow: 'hidden' }}>
        {items === null && <div style={{ padding: '14px 16px', fontSize: 11.5, color: 'var(--text3)' }}>Loading library…</div>}
        {items !== null && count === 0 && !error && (
          <div style={{ padding: '22px 16px', fontSize: 12, color: 'var(--text3)', textAlign: 'center', lineHeight: 1.7 }}>
            No saved queries yet.<br />Save a query from Ask AURA and it appears here for one-click reuse.
          </div>
        )}
        {(items ?? []).map((q, i) => (
          <div key={q.id} style={{ display: 'flex', flexDirection: 'column', gap: 5, padding: '11px 16px', borderTop: i === 0 ? 'none' : '1px solid var(--hair)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ flex: 'none', fontSize: 12, color: q.starred ? 'var(--warn)' : 'var(--text3)' }}>{q.starred ? '★' : '☆'}</span>
              <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{q.name || '(untitled)'}</span>
              <div style={{ flex: 1 }} />
              {q.prompt && q.prompt !== q.name && <span style={{ fontSize: 11, color: 'var(--text3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 320 }}>{q.prompt}</span>}
            </div>
            {q.sql && <div className="aw-mono" style={{ fontSize: 10.5, color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--hair)', padding: '5px 9px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{q.sql}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
