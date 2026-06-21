import { Suspense, useCallback, useMemo, useRef } from 'react';
import { DockviewReact, type DockviewReadyEvent, type IDockviewPanelProps, type DockviewApi } from 'dockview-react';
import 'dockview-react/dist/styles/dockview.css';
import { CockpitProvider } from './CockpitProvider';
import { PanelErrorBoundary } from './PanelErrorBoundary';
import { PANEL_REGISTRY, type PanelId } from './panels/registry';
import { persistLayout, restoreLayout, DEFAULT_LAYOUTS } from './layoutStore';
import './terminal.css';

const LAYOUT_KEY = 'default';

function buildComponents(): Record<string, React.FC<IDockviewPanelProps>> {
  const out: Record<string, React.FC<IDockviewPanelProps>> = {};
  (Object.keys(PANEL_REGISTRY) as PanelId[]).forEach((id) => {
    const def = PANEL_REGISTRY[id];
    const Lazy = def.component;
    const Wrapped: React.FC<IDockviewPanelProps> = (props) => (
      <PanelErrorBoundary panelTitle={def.title}>
        <Suspense fallback={<div className="panel-loading">Loading…</div>}>
          <Lazy {...props} />
        </Suspense>
      </PanelErrorBoundary>
    );
    out[id] = Wrapped;
  });
  return out;
}

export function TerminalWorkspace() {
  const apiRef = useRef<DockviewApi | null>(null);
  const components = useMemo(buildComponents, []);

  const onReady = useCallback((event: DockviewReadyEvent) => {
    apiRef.current = event.api;
    const restored = restoreLayout(LAYOUT_KEY, event.api);
    if (!restored) DEFAULT_LAYOUTS.analyst(event.api);
    event.api.onDidLayoutChange(() => persistLayout(LAYOUT_KEY, event.api));
  }, []);

  return (
    <CockpitProvider>
      <div className="aura-terminal" data-testid="terminal-workspace">
        <DockviewReact
          className="dockview-theme-dark"
          components={components}
          onReady={onReady}
        />
      </div>
    </CockpitProvider>
  );
}

export default TerminalWorkspace;
