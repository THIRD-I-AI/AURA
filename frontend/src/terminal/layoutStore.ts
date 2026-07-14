import type { DockviewApi } from 'dockview-react';

const KEY = (name: string) => `aura.terminal.layout.${name}`;

export function persistLayout(name: string, api: Pick<DockviewApi, 'toJSON'>): void {
  try {
    localStorage.setItem(KEY(name), JSON.stringify(api.toJSON()));
  } catch (err) {
    console.warn('[terminal] failed to persist layout', err);
  }
}

export function restoreLayout(name: string, api: Pick<DockviewApi, 'fromJSON'>): boolean {
  const raw = localStorage.getItem(KEY(name));
  if (!raw) return false;
  try {
    api.fromJSON(JSON.parse(raw));
    return true;
  } catch (err) {
    console.warn('[terminal] corrupt saved layout, discarding', err);
    localStorage.removeItem(KEY(name));
    return false;
  }
}

export const LAYOUT_NAMES = ['analyst', 'auditor', 'ops'] as const;

export const DEFAULT_LAYOUTS: Record<
  (typeof LAYOUT_NAMES)[number],
  (api: Pick<DockviewApi, 'addPanel'>) => void
> = {
  analyst: (api) => {
    // Constellation leads — the interactive knowledge-graph is the centerpiece.
    api.addPanel({ id: 'constellation', component: 'constellation', title: 'Constellation' });
    api.addPanel({ id: 'datasets', component: 'datasets', title: 'Datasets', position: { referencePanel: 'constellation', direction: 'right' } });
    api.addPanel({ id: 'query', component: 'query', title: 'Query', position: { referencePanel: 'constellation', direction: 'below' } });
    api.addPanel({ id: 'findings', component: 'findings', title: 'Findings', position: { referencePanel: 'datasets', direction: 'below' } });
    api.addPanel({ id: 'livefeed', component: 'livefeed', title: 'Live Feed', position: { referencePanel: 'findings', direction: 'below' } });
  },
  auditor: (api) => {
    // Audit command deck leads — risk-sorted triage + verification is the auditor centerpiece.
    api.addPanel({ id: 'audit', component: 'audit', title: 'Audit' });
    api.addPanel({ id: 'findings', component: 'findings', title: 'Findings', position: { referencePanel: 'audit', direction: 'right' } });
    api.addPanel({ id: 'query', component: 'query', title: 'Query', position: { referencePanel: 'audit', direction: 'below' } });
    api.addPanel({ id: 'livefeed', component: 'livefeed', title: 'Live Feed', position: { referencePanel: 'findings', direction: 'below' } });
  },
  ops: (api) => {
    // Pipeline command deck leads — the live service DAG is the ops centerpiece.
    api.addPanel({ id: 'pipeline', component: 'pipeline', title: 'Pipeline' });
    api.addPanel({ id: 'livefeed', component: 'livefeed', title: 'Live Feed', position: { referencePanel: 'pipeline', direction: 'below' } });
    api.addPanel({ id: 'findings', component: 'findings', title: 'Findings', position: { referencePanel: 'livefeed', direction: 'right' } });
    api.addPanel({ id: 'query', component: 'query', title: 'Query', position: { referencePanel: 'pipeline', direction: 'right' } });
  },
};
