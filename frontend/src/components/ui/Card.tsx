import React from 'react';
import '../../styles/components.css';

interface CardProps {
  children: React.ReactNode;
  className?: string;
}

export const Card: React.FC<CardProps> = ({ children, className = '' }) => (
  <div className={`card ${className}`}>{children}</div>
);

interface CardHeaderProps {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}

export const CardHeader: React.FC<CardHeaderProps> = ({ title, subtitle, action }) => (
  <div className="card-header">
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <div>
        <h3 className="card-header-title">{title}</h3>
        {subtitle && <p style={{ margin: '0.5rem 0 0 0', color: 'var(--text-secondary)', fontSize: 'var(--font-sm)' }}>{subtitle}</p>}
      </div>
      {action}
    </div>
  </div>
);

interface CardBodyProps {
  children: React.ReactNode;
}

export const CardBody: React.FC<CardBodyProps> = ({ children }) => (
  <div className="card-body">{children}</div>
);

interface CardFooterProps {
  children: React.ReactNode;
}

export const CardFooter: React.FC<CardFooterProps> = ({ children }) => (
  <div className="card-footer">{children}</div>
);

export default Card;
