/**
 * Toast Notification UI
 * ======================
 * Renders the toast queue from ToastContext in the bottom-right corner.
 * Import <ToastContainer /> once in App.tsx — it reads from the context.
 */
import { useToast, type Toast, type ToastVariant } from '../../contexts/ToastContext';

const ICONS: Record<ToastVariant, string> = {
  success: '✓',
  error: '✕',
  warning: '⚠',
  info: 'ℹ',
};

const COLORS: Record<ToastVariant, { bg: string; border: string; icon: string }> = {
  success: {
    bg: 'var(--color-success-50)',
    border: 'var(--color-success-200)',
    icon: 'var(--color-success-600)',
  },
  error: {
    bg: 'var(--color-error-50)',
    border: 'var(--color-error-200)',
    icon: 'var(--color-error-600)',
  },
  warning: {
    bg: 'var(--color-warning-50)',
    border: 'var(--color-warning-200)',
    icon: 'var(--color-warning-600)',
  },
  info: {
    bg: 'var(--color-info-50)',
    border: 'var(--color-primary-200)',
    icon: 'var(--color-info-500)',
  },
};

function ToastItem({ toast }: { toast: Toast }) {
  const { dismiss } = useToast();
  const colors = COLORS[toast.variant];

  return (
    <div
      role="alert"
      aria-live="assertive"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 'var(--space-3)',
        padding: 'var(--space-3) var(--space-4)',
        background: colors.bg,
        border: `1px solid ${colors.border}`,
        borderRadius: 'var(--radius-lg)',
        boxShadow: 'var(--shadow-lg)',
        animation: 'toast-slide-in var(--duration-slow) var(--easing-bounce)',
        minWidth: '280px',
        maxWidth: '400px',
        pointerEvents: 'all',
      }}
    >
      {/* Icon */}
      <span
        style={{
          flexShrink: 0,
          width: '1.25rem',
          height: '1.25rem',
          borderRadius: 'var(--radius-full)',
          background: colors.icon,
          color: '#fff',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '0.625rem',
          fontWeight: 'var(--weight-bold)',
          marginTop: '2px',
        }}
      >
        {ICONS[toast.variant]}
      </span>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <p
          style={{
            margin: 0,
            fontSize: 'var(--font-sm)',
            fontWeight: 'var(--weight-semibold)',
            color: 'var(--text-primary)',
            lineHeight: 'var(--line-tight)',
          }}
        >
          {toast.title}
        </p>
        {toast.message && (
          <p
            style={{
              margin: '2px 0 0',
              fontSize: 'var(--font-xs)',
              color: 'var(--text-secondary)',
              lineHeight: 'var(--line-normal)',
            }}
          >
            {toast.message}
          </p>
        )}
      </div>

      {/* Dismiss */}
      <button
        onClick={() => dismiss(toast.id)}
        aria-label="Dismiss notification"
        style={{
          flexShrink: 0,
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          padding: '2px',
          color: 'var(--text-tertiary)',
          fontSize: '1rem',
          lineHeight: 1,
          borderRadius: 'var(--radius-sm)',
        }}
      >
        ×
      </button>
    </div>
  );
}

export function ToastContainer() {
  const { toasts } = useToast();

  if (toasts.length === 0) return null;

  return (
    <div
      aria-label="Notifications"
      style={{
        position: 'fixed',
        bottom: 'var(--space-6)',
        right: 'var(--space-6)',
        zIndex: 'var(--z-notification)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-2)',
        pointerEvents: 'none',
      }}
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} />
      ))}
    </div>
  );
}

export default ToastContainer;
