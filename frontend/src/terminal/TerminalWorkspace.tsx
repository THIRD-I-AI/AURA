import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { DockviewReact, type DockviewReadyEvent, type IDockviewPanelProps, type DockviewApi } from 'dockview-react';
import 'dockview-react/dist/styles/dockview.css';
import { CockpitProvider } from './CockpitProvider';
import { CockpitTopBar } from './CockpitTopBar';
import { PanelErrorBoundary } from './PanelErrorBoundary';
import { PANEL_REGISTRY, type PanelId } from './panels/registry';
import { persistLayout, restoreLayout, DEFAULT_LAYOUTS, LAYOUT_NAMES } from './layoutStore';
import { TerminalCommandPalette } from './TerminalCommandPalette';
import { buildTerminalCommands } from './commands';
import { useViewport } from '../shell/ViewportProvider';
import { MobileTerminalStack } from './MobileTerminalStack';
import './terminal.css';

// Bumped to v2 when the Constellation panel joined the default layout, so the
// new constellation-led default surfaces once for everyone (old saved layouts
// predate the panel). Custom layouts re-persist under this key thereafter.
const LAYOUT_KEY = 'default.v2';

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
  const navigate = useNavigate();
  const [paletteOpen, setPaletteOpen] = useState(false);
  // dockview's multi-panel grid can't reflow to a phone; below the 'standard'
  // viewport class we render a single-panel stacked fallback instead. Reads the
  // app-wide viewport anchor so there is one source of truth (S49).
  const isMobile = !useViewport().atLeast('standard');

  const onReady = useCallback((event: DockviewReadyEvent) => {
    apiRef.current = event.api;
    const restored = restoreLayout(LAYOUT_KEY, event.api);
    if (!restored) DEFAULT_LAYOUTS.analyst(event.api);
    event.api.onDidLayoutChange(() => persistLayout(LAYOUT_KEY, event.api));
  }, []);

  const openPanel = useCallback((id: PanelId) => {
    const api = apiRef.current;
    if (!api) return;
    const existing = api.getPanel(id);
    if (existing) {
      existing.api.setActive();
    } else {
      api.addPanel({ id, component: id, title: PANEL_REGISTRY[id].title });
    }
  }, []);

  const applyLayout = useCallback((n: (typeof LAYOUT_NAMES)[number]) => {
    const api = apiRef.current;
    if (api) {
      api.clear();
      DEFAULT_LAYOUTS[n](api);
    }
  }, []);

  const resetLayout = useCallback(() => applyLayout('analyst'), [applyLayout]);

  const commands = useMemo(
    () => buildTerminalCommands({ openPanel, applyLayout, resetLayout, back: () => navigate('/app') }),
    [openPanel, applyLayout, resetLayout, navigate],
  );

  // Global Cmd/Ctrl+K shortcut to open the palette.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  return (
    <CockpitProvider>
      <div
        className="aura-terminal"
        data-testid="terminal-workspace"
        data-palette-open={paletteOpen}
        data-mobile={isMobile ? 'true' : undefined}
      >
        <CockpitTopBar
          onApplyLayout={applyLayout}
          onOpenPalette={() => setPaletteOpen(true)}
          onBack={() => navigate('/app')}
        />
        {isMobile ? (
          <MobileTerminalStack />
        ) : (
          <DockviewReact
            className="dockview-theme-dark"
            components={components}
            onReady={onReady}
          />
        )}
        <TerminalCommandPalette
          open={paletteOpen}
          onClose={() => setPaletteOpen(false)}
          commands={commands}
        />
      </div>
    </CockpitProvider>
  );
}

export default TerminalWorkspace;
