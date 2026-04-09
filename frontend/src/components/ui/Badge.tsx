import React from 'react';

type BadgeVariant = 'default' | 'blue' | 'green' | 'yellow' | 'red' | 'purple' | 'cyan';

/** Map legacy color props to new dark variants */
const COLOR_MAP: Record<string, BadgeVariant> = {
  primary: 'blue',
  success: 'green',
  warning: 'yellow',
  error:   'red',
  info:    'blue',
  default: 'default',
  blue:    'blue',
  green:   'green',
  yellow:  'yellow',
  red:     'red',
  purple:  'purple',
  cyan:    'cyan',
};

interface BadgeProps {
  color?: string;
  variant?: BadgeVariant;
  children: React.ReactNode;
  icon?: React.ReactNode;
  /** Show an animated pulse dot */
  live?: boolean;
}

export const Badge: React.FC<BadgeProps> = ({ color = 'default', variant, children, icon, live }) => {
  const cls = `badge badge-${variant ?? COLOR_MAP[color] ?? 'default'}`;
  return (
    <span className={cls}>
      {live && <span className="badge-dot badge-dot--pulse" />}
      {icon && !live && <span style={{ display: 'flex', alignItems: 'center' }}>{icon}</span>}
      {children}
    </span>
  );
};

export default Badge;
