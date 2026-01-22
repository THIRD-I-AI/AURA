import React from 'react';
import '../../styles/components.css';

type BadgeColor = 'primary' | 'success' | 'warning' | 'error';

interface BadgeProps {
  color?: BadgeColor;
  children: React.ReactNode;
  icon?: React.ReactNode;
}

/**
 * Badge Component for status indicators
 */
export const Badge: React.FC<BadgeProps> = ({ color = 'primary', children, icon }) => (
  <span className={`badge badge-${color}`}>
    {icon && <span>{icon}</span>}
    {children}
  </span>
);

export default Badge;
