import { PANEL_REGISTRY, type PanelId } from './panels/registry';
import { LAYOUT_NAMES } from './layoutStore';

export interface TerminalCommand {
  id: string;
  label: string;
  group: 'panel' | 'layout' | 'action';
  run: () => void;
}

export function buildTerminalCommands(handlers: {
  openPanel: (id: PanelId) => void;
  applyLayout: (n: (typeof LAYOUT_NAMES)[number]) => void;
  resetLayout: () => void;
  back: () => void;
}): TerminalCommand[] {
  const panelCmds: TerminalCommand[] = (Object.keys(PANEL_REGISTRY) as PanelId[]).map((id) => ({
    id: `open-${id}`,
    label: `Open ${PANEL_REGISTRY[id].title}`,
    group: 'panel',
    run: () => handlers.openPanel(id),
  }));
  const layoutCmds: TerminalCommand[] = LAYOUT_NAMES.map((n) => ({
    id: `layout-${n}`,
    label: `Layout: ${n}`,
    group: 'layout',
    run: () => handlers.applyLayout(n),
  }));
  return [
    ...panelCmds,
    ...layoutCmds,
    { id: 'reset-layout', label: 'Reset layout', group: 'action', run: handlers.resetLayout },
    { id: 'back', label: 'Back to app', group: 'action', run: handlers.back },
  ];
}
