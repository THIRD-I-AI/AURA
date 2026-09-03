/* Shell nav config — shared between WorkbenchNav (renders the rail) and
   CommandPalette (flattens it into "Go to X" commands). */
import {
  LayoutDashboard, SquareTerminal, MessageSquare, BarChart3, BookOpen, History,
  ShieldCheck, GitBranch, BadgeCheck, AlertTriangle, Workflow, Activity, Wrench,
  CalendarClock, Link2, DollarSign, Plug, FolderOpen, Share2, Database,
  type LucideIcon,
} from 'lucide-react';

export const NAV_GROUPS: [string, string[]][] = [
  ['WORKSPACE', ['Cockpit', 'Terminal', 'Ask AURA', 'Dashboards', 'Library', 'Query History']],
  ['AUDIT', ['Audit Workbench', 'Counterfactuals', 'Certificates', 'Exception Queue']],
  ['OPERATE', ['Pipelines', 'Streaming', 'Healing Queue', 'Scheduler', 'Webhooks', 'Cost']],
  ['DATA', ['Connectors', 'Files & Data', 'Lineage', 'Metadata Store']],
];

/* Icon per nav destination — purely visual scanability, no data behind it. */
export const NAV_ICONS: Record<string, LucideIcon> = {
  'Cockpit': LayoutDashboard, 'Terminal': SquareTerminal, 'Ask AURA': MessageSquare,
  'Dashboards': BarChart3, 'Library': BookOpen, 'Query History': History,
  'Audit Workbench': ShieldCheck, 'Counterfactuals': GitBranch, 'Certificates': BadgeCheck,
  'Exception Queue': AlertTriangle, 'Pipelines': Workflow, 'Streaming': Activity,
  'Healing Queue': Wrench, 'Scheduler': CalendarClock, 'Webhooks': Link2, 'Cost': DollarSign,
  'Connectors': Plug, 'Files & Data': FolderOpen, 'Lineage': Share2, 'Metadata Store': Database,
};
