import { lazy } from 'react';
import { Terminal, Database, ShieldAlert, Activity, Waypoints, type LucideIcon } from 'lucide-react';
import type { IDockviewPanelProps } from 'dockview-react';

export type PanelId = 'query' | 'datasets' | 'findings' | 'livefeed' | 'constellation';

export interface PanelDef {
  title: string;
  icon: LucideIcon;
  component: React.LazyExoticComponent<React.FC<IDockviewPanelProps>>;
}

export const PANEL_REGISTRY: Record<PanelId, PanelDef> = {
  query:         { title: 'Query',         icon: Terminal,    component: lazy(() => import('./QueryPanel')) },
  datasets:      { title: 'Datasets',      icon: Database,    component: lazy(() => import('./DatasetsPanel')) },
  findings:      { title: 'Findings',      icon: ShieldAlert, component: lazy(() => import('./FindingsPanel')) },
  livefeed:      { title: 'Live Feed',     icon: Activity,    component: lazy(() => import('./LiveFeedPanel')) },
  constellation: { title: 'Constellation', icon: Waypoints,   component: lazy(() => import('./ConstellationPanel')) },
};

export const PANEL_IDS = Object.keys(PANEL_REGISTRY) as PanelId[];
