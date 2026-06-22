import { useEffect } from 'react';
import { useAuraStore } from '../../store';

interface QueryRecord {
  id: string;
  prompt: string;
  status: string;
  rows: number;
  timestamp: string;
}

// Query/Library rail: the most recent query-history entries, reopenable.
export default function HistoryRail() {
  const {
    state: { queryHistory },
    actions: { fetchQueryHistory },
  } = useAuraStore();

  useEffect(() => {
    fetchQueryHistory(20);
  }, [fetchQueryHistory]);

  const items = (queryHistory as QueryRecord[]).slice(0, 12);

  return (
    <div className="rail-section">
      <h4 className="rail-section__title">Recent queries</h4>
      {items.length === 0 ? (
        <p className="rail-empty">No queries yet.</p>
      ) : (
        <ul className="rail-list">
          {items.map((q) => (
            <li key={q.id}>
              <span className="rail-list__item" title={q.prompt}>
                <span className={`rail-dot rail-dot--${q.status}`} />
                {q.prompt || '(query)'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
