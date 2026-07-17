import { useState } from 'react';
import { SystemRadar, useSystemPulse } from '../components/radar';

/**
 * Ambient live-system HUD for the terminal cockpit. Uses the SHARED SystemRadar
 * component (same one the workbench cockpit mounts) fed by the self-contained
 * useSystemPulse hook, so it reflects new pipelines / changed health / fresh
 * drift on its own and degrades honestly when the gateway is unreachable — it
 * never throws into the terminal render tree.
 *
 * Lazy-loaded by TerminalWorkspace, so the mocked workspace unit test (which
 * renders synchronously) never evaluates the pulse hook.
 */
export default function TerminalRadarRail() {
  const [open, setOpen] = useState(true);
  const model = useSystemPulse(10000, open); // pause polling while collapsed
  const down = model.services.filter((s) => s.up === false).length;
  const drifting = model.sources.filter((s) => s.severity !== 'none').length;

  return (
    <div className="terminal-radar-hud" data-testid="terminal-radar-hud" data-open={open}>
      <button
        className="terminal-radar-toggle"
        onClick={() => setOpen((o) => !o)}
        title={open ? 'Collapse system radar' : 'Expand system radar'}
      >
        <span
          className="terminal-radar-dot"
          style={{
            background:
              model.gatewayUp === false
                ? 'var(--danger, #ef4444)'
                : model.gatewayUp
                  ? 'var(--accent, #22c55e)'
                  : 'var(--text3, #5a6675)',
          }}
        />
        <span className="terminal-radar-label">SYSTEM RADAR</span>
        <span className="terminal-radar-meta">
          {model.services.length}·{model.sources.length}
          {down > 0 ? ` · ${down}↓` : ''}
          {drifting > 0 ? ` · ${drifting}⚠` : ''}
        </span>
        <span className="terminal-radar-caret">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="terminal-radar-body">
          <SystemRadar model={model} size={232} />
        </div>
      )}
    </div>
  );
}
