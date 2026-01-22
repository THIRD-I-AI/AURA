import React from 'react';
import '../../styles/components.css';

type AlertType = 'info' | 'success' | 'warning' | 'error';

interface AlertProps {
  type: AlertType;
  title: string;
  message: string;
  onClose?: () => void;
  action?: {
    label: string;
    onClick: () => void;
  };
}

const alertIcons: Record<AlertType, string> = {
  info: 'ℹ️',
  success: '✓',
  warning: '⚠',
  error: '✕',
};

/**
 * Alert Component for displaying notifications
 */
export const Alert: React.FC<AlertProps> = ({
  type,
  title,
  message,
  onClose,
  action,
}) => {
  return (
    <div className={`alert alert-${type}`} role="alert">
      <span className="alert-icon">{alertIcons[type]}</span>
      <div className="alert-content">
        <strong>{title}</strong>
        <p style={{ margin: '0.25rem 0 0 0', opacity: 0.9 }}>{message}</p>
      </div>
      <div style={{ display: 'flex', gap: 'var(--space-2)', marginLeft: 'auto' }}>
        {action && (
          <button
            onClick={action.onClick}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'inherit',
              cursor: 'pointer',
              fontWeight: 'bold',
              textDecoration: 'underline',
            }}
          >
            {action.label}
          </button>
        )}
        {onClose && (
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'inherit',
              cursor: 'pointer',
              fontSize: '1.5rem',
              padding: 0,
              lineHeight: 1,
            }}
            aria-label="Close alert"
          >
            ×
          </button>
        )}
      </div>
    </div>
  );
};

export default Alert;
