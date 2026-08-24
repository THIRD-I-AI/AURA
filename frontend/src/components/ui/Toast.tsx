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

const COLORS: Record<ToastVariant, { dot: string; container: string }> = {
  success: {
    dot: 'bg-signal',
    container: 'border-signal/30 bg-signal/10',
  },
  error: {
    dot: 'bg-danger',
    container: 'border-danger/30 bg-danger/10',
  },
  warning: {
    dot: 'bg-warn',
    container: 'border-warn/30 bg-warn/10',
  },
  info: {
    dot: 'bg-info',
    container: 'border-info/30 bg-info/10',
  },
};

function ToastItem({ toast }: { toast: Toast }) {
  const { dismiss } = useToast();
  const colors = COLORS[toast.variant];

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={`flex items-start gap-3 py-3 px-4 rounded-none min-w-[280px] max-w-[400px] pointer-events-auto border border-border ${colors.container}`}
      style={{
        animation: 'toast-slide-in var(--duration-slow) var(--easing-bounce)',
      }}
    >
      {/* Icon */}
      <span
        className={`flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-2xs font-bold text-white ${colors.dot}`}
        style={{
          marginTop: '2px',
        }}
      >
        {ICONS[toast.variant]}
      </span>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <p className="m-0 text-sm font-semibold text-text-primary leading-tight">
          {toast.title}
        </p>
        {toast.message && (
          <p className="m-0 mt-0.5 text-xs text-text-secondary leading-normal">
            {toast.message}
          </p>
        )}
      </div>

      {/* Dismiss */}
      <button
        onClick={() => dismiss(toast.id)}
        aria-label="Dismiss notification"
        className="flex-shrink-0 bg-transparent border-none cursor-pointer p-0.5 text-text-tertiary text-base leading-none rounded-sm"
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
      className="fixed bottom-6 right-6 flex flex-col gap-2 pointer-events-none"
      style={{
        zIndex: 'var(--z-notification)',
      }}
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} />
      ))}
    </div>
  );
}

export default ToastContainer;
