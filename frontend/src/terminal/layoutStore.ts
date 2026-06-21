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
    api.addPanel({ id: 'query', component: 'query', title: 'Query' });
    api.addPanel({ id: 'datasets', component: 'datasets', title: 'Datasets', position: { referencePanel: 'query', direction: 'right' } });
    api.addPanel({ id: 'findings', component: 'findings', title: 'Findings', position: { referencePanel: 'query', direction: 'below' } });
    api.addPanel({ id: 'livefeed', component: 'livefeed', title: 'Live Feed', position: { referencePanel: 'datasets', direction: 'below' } });
  },
  auditor: (api) => {
    api.addPanel({ id: 'findings', component: 'findings', title: 'Findings' });
    api.addPanel({ id: 'datasets', component: 'datasets', title: 'Datasets', position: { referencePanel: 'findings', direction: 'right' } });
    api.addPanel({ id: 'query', component: 'query', title: 'Query', position: { referencePanel: 'findings', direction: 'below' } });
    api.addPanel({ id: 'livefeed', component: 'livefeed', title: 'Live Feed', position: { referencePanel: 'datasets', direction: 'below' } });
  },
  ops: (api) => {
    api.addPanel({ id: 'livefeed', component: 'livefeed', title: 'Live Feed' });
    api.addPanel({ id: 'findings', component: 'findings', title: 'Findings', position: { referencePanel: 'livefeed', direction: 'right' } });
    api.addPanel({ id: 'query', component: 'query', title: 'Query', position: { referencePanel: 'livefeed', direction: 'below' } });
    api.addPanel({ id: 'datasets', component: 'datasets', title: 'Datasets', position: { referencePanel: 'findings', direction: 'below' } });
  },
};
