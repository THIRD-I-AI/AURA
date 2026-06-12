import React from 'react';

export interface CardProps {
  title?: string;
  subtitle?: string;
  /** Evidentiary accent-left edge (3px) — signal/warn/danger/info. */
  accent?: 'signal' | 'warn' | 'danger' | 'info';
  actions?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}

export const Card: React.FC<CardProps> = ({ title, subtitle, accent, actions, className, children }) => (
  <section className={['ui-card', accent && `ui-card--${accent}`, className].filter(Boolean).join(' ')}>
    {(title || actions) && (
      <header className="ui-card__header">
        <div>
          {title && <h3 className="ui-card__title">{title}</h3>}
          {subtitle && <p className="ui-card__subtitle">{subtitle}</p>}
        </div>
        {actions && <div className="ui-card__actions">{actions}</div>}
      </header>
    )}
    <div className="ui-card__body">{children}</div>
  </section>
);
