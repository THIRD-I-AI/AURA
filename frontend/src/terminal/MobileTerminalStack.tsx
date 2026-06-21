import { Suspense, useState } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import { PANEL_REGISTRY, PANEL_IDS, type PanelId } from './panels/registry';
import { PanelErrorBoundary } from './PanelErrorBoundary';
import './terminal-mobile.css';

// Every panel ignores its dockview props (all declared `_props`) and reads state
// from CockpitProvider context + services, so an empty shim renders them
// correctly outside the dockview engine. TerminalWorkspace keeps this subtree
// inside CockpitProvider, so the cross-filter bus still works.
const EMPTY_PANEL_PROPS = {} as unknown as IDockviewPanelProps;

/**
 * Phone/tablet fallback for the Terminal cockpit. dockview's multi-panel grid is
 * unusable below ~860px, so this renders one full-width panel at a time behind a
 * scrollable tab bar, with a banner explaining it's a reduced view of a desktop
 * tool. Mounted by TerminalWorkspace in place of <DockviewReact>.
 */
export function MobileTerminalStack() {
  const [active, setActive] = useState<PanelId>(PANEL_IDS[0]);
  const ActivePanel = PANEL_REGISTRY[active].component;

  return (
    <div className="terminal-mobile" data-testid="terminal-mobile">
      <p className="terminal-mobile__banner" role="note">
        <span className="terminal-mobile__banner-icon" aria-hidden>⚡</span>
        <span>
          The Terminal is a desktop cockpit — showing one panel at a time. Open
          AURA on a larger screen for the full multi-panel workspace.
        </span>
      </p>

      <nav className="terminal-mobile__tabs" aria-label="Terminal panels">
        {PANEL_IDS.map((id) => {
          const def = PANEL_REGISTRY[id];
          const Icon = def.icon;
          const isActive = id === active;
          return (
            <button
              key={id}
              type="button"
              className={`terminal-mobile__tab${isActive ? ' is-active' : ''}`}
              aria-pressed={isActive}
              onClick={() => setActive(id)}
            >
              <Icon size={14} aria-hidden />
              <span>{def.title}</span>
            </button>
          );
        })}
      </nav>

      <div className="terminal-mobile__panel">
        <PanelErrorBoundary panelTitle={PANEL_REGISTRY[active].title}>
          <Suspense fallback={<div className="panel-loading">Loading…</div>}>
            <ActivePanel {...EMPTY_PANEL_PROPS} />
          </Suspense>
        </PanelErrorBoundary>
      </div>
    </div>
  );
}

export default MobileTerminalStack;
