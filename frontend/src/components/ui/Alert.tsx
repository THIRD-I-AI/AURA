import React from 'react';

type AlertType = 'info' | 'success' | 'warning' | 'error';

interface AlertProps {
  type: AlertType;
  title: string;
  message?: string;
  onClose?: () => void;
  action?: { label: string; onClick: () => void };
}

const ICONS: Record<AlertType, React.ReactNode> = {
  info: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.3"/>
      <path d="M8 7v4M8 5h.01" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  success: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.3"/>
      <path d="M5 8l2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  warning: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M8 2L14.5 13.5H1.5L8 2z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
      <path d="M8 6.5v3M8 11h.01" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  error: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.3"/>
      <path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
};

const TYPE_COLOR: Record<AlertType, string> = {
  info:    'var(--blue)',
  success: 'var(--green)',
  warning: 'var(--yellow)',
  error:   'var(--red)',
};

export const Alert: React.FC<AlertProps> = ({ type, title, message, onClose, action }) => (
  <div className={`alert alert-${type}`} role="alert">
    <span style={{ color: TYPE_COLOR[type], display: 'flex', alignItems: 'center', flexShrink: 0, marginTop: 1 }}>
      {ICONS[type]}
    </span>
    <div className="alert-body">
      <div className="alert-title">{title}</div>
      {message && <div className="alert-message">{message}</div>}
    </div>
    <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center', marginLeft: 'auto', flexShrink: 0 }}>
      {action && (
        <button
          onClick={action.onClick}
          style={{ background: 'none', border: 'none', color: TYPE_COLOR[type], cursor: 'pointer', fontSize: 'var(--font-xs)', fontWeight: 600, padding: 0 }}
        >
          {action.label}
        </button>
      )}
      {onClose && (
        <button
          onClick={onClose}
          aria-label="Close"
          style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer', fontSize: '1.1rem', lineHeight: 1, padding: 0 }}
        >
          ×
        </button>
      )}
    </div>
  </div>
);

export default Alert;
