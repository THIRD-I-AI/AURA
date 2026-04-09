import React from 'react';

interface CardProps {
  children: React.ReactNode;
  className?: string;
  /** extra inline styles on the root element */
  style?: React.CSSProperties;
  /** remove the default padding from CardBody */
  noPadding?: boolean;
}

export const Card: React.FC<CardProps> = ({ children, className = '', style }) => (
  <div className={`card ${className}`} style={style}>{children}</div>
);

interface CardHeaderProps {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
}

export const CardHeader: React.FC<CardHeaderProps> = ({ title, subtitle, action }) => (
  <div className="card-header">
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 'var(--space-3)' }}>
      <div style={{ minWidth: 0 }}>
        <h3 className="card-header-title">{title}</h3>
        {subtitle && (
          <p style={{ margin: '3px 0 0', color: 'var(--text-tertiary)', fontSize: 'var(--font-xs)', lineHeight: 'var(--line-snug)' }}>
            {subtitle}
          </p>
        )}
      </div>
      {action && <div style={{ flexShrink: 0 }}>{action}</div>}
    </div>
  </div>
);

export const CardBody: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <div className="card-body" style={style}>{children}</div>
);

export const CardFooter: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="card-footer">{children}</div>
);

export default Card;
