import { useEffect, useState } from 'react';
import { useSystemHealth, healthHint } from '../../hooks/useSystemHealth';
import { savedQueryService, type SavedQuery } from '../../services/api';

// Dashboard rail: system pulse + recent saved queries + a quick-ask shortcut.
export default function DashboardRail() {
  const health = useSystemHealth();
  const [recent, setRecent] = useState<SavedQuery[]>([]);

  useEffect(() => {
    savedQueryService
      .list()
      .then((qs) => setRecent(qs.slice(0, 6)))
      .catch(() => {});
  }, []);

  return (
    <>
      <div className="rail-section">
        <h4 className="rail-section__title">System pulse</h4>
        <div className={`rail-pulse rail-pulse--${health.isOnline ? 'on' : 'off'}`}>
          <span className="rail-pulse__dot" />
          {healthHint(health.status)}
        </div>
      </div>

      <div className="rail-section">
        <h4 className="rail-section__title">Recent saved queries</h4>
        {recent.length === 0 ? (
          <p className="rail-empty">Nothing saved yet.</p>
        ) : (
          <ul className="rail-list">
            {recent.map((q) => (
              <li key={q.id}>
                <a className="rail-list__item" href="/app/library" title={q.name}>
                  {q.starred ? '★ ' : ''}
                  {q.name}
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="rail-section">
        <h4 className="rail-section__title">Ask</h4>
        <a className="rail-quick__btn" href="/app/chat">Ask about your data →</a>
      </div>
    </>
  );
}
