/**
 * PageType — shared nav-destination union, relocated out of the (now
 * deleted) classic Layout/AppLayout.tsx dead code so the shell rail
 * registry, PipelinesPanel, and EngagementsLaunchpad keep a real type
 * instead of importing from a component that no longer exists.
 */
export type PageType =
  | 'dashboard'
  | 'chat'
  | 'files'
  | 'queries'
  | 'library'
  | 'dashboards'
  | 'lineage'
  | 'cost'
  | 'settings'
  | 'agent'
  | 'pipelines'
  | 'streaming'
  | 'webhooks'
  | 'counterfactual'
  | 'audit-hitl'
  | 'audit-service'
  | 'healing-queue';
