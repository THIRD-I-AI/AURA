import { useSystemHealth, healthHint } from '../../hooks/useSystemHealth';

// Fallback rail: quick actions + system pulse. Pages without a specific slot
// still get something useful in the reclaimed width.
export default function DefaultRail() {
  const health = useSystemHealth();
  return (
    <>
      <div className="rail-section">
        <h4 className="rail-section__title">Quick actions</h4>
        <div className="rail-quick">
          <a className="rail-quick__btn" href="/app/chat">Ask about your data</a>
          <a className="rail-quick__btn" href="/app/files">Upload a file</a>
          <a className="rail-quick__btn" href="/app/terminal">Open the Terminal</a>
        </div>
      </div>
      <div className="rail-section">
        <h4 className="rail-section__title">System pulse</h4>
        <div className={`rail-pulse rail-pulse--${health.isOnline ? 'on' : 'off'}`}>
          <span className="rail-pulse__dot" />
          {healthHint(health.status)}
        </div>
      </div>
    </>
  );
}
