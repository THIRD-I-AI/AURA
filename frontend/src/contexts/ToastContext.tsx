/**
 * Toast Notification System
 * ==========================
 * Global context-based toast notifications with auto-dismiss.
 *
 * Usage:
 *   const toast = useToast();
 *   toast.success('File uploaded');
 *   toast.error('Connection failed', { duration: 8000 });
 *   toast.info('Query running...');
 *   toast.warning('Rate limit approaching');
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
} from 'react';

export type ToastVariant = 'success' | 'error' | 'warning' | 'info';

export interface Toast {
  id: string;
  variant: ToastVariant;
  title: string;
  message?: string;
  duration: number;     // ms; 0 = persistent
  createdAt: number;
}

interface ToastOptions {
  message?: string;
  duration?: number;
}

interface ToastContextValue {
  toasts: Toast[];
  success: (title: string, opts?: ToastOptions) => string;
  error: (title: string, opts?: ToastOptions) => string;
  warning: (title: string, opts?: ToastOptions) => string;
  info: (title: string, opts?: ToastOptions) => string;
  dismiss: (id: string) => void;
  dismissAll: () => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
  }, []);

  const dismissAll = useCallback(() => {
    timersRef.current.forEach(clearTimeout);
    timersRef.current.clear();
    setToasts([]);
  }, []);

  const add = useCallback(
    (
      variant: ToastVariant,
      title: string,
      { message, duration = 4000 }: ToastOptions = {}
    ): string => {
      const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      const toast: Toast = { id, variant, title, message, duration, createdAt: Date.now() };
      setToasts((prev) => [...prev.slice(-9), toast]); // max 10 toasts

      if (duration > 0) {
        const timer = setTimeout(() => dismiss(id), duration);
        timersRef.current.set(id, timer);
      }
      return id;
    },
    [dismiss]
  );

  const value: ToastContextValue = {
    toasts,
    success: (t, o) => add('success', t, o),
    error: (t, o) => add('error', t, { duration: 6000, ...o }),
    warning: (t, o) => add('warning', t, o),
    info: (t, o) => add('info', t, o),
    dismiss,
    dismissAll,
  };

  return (
    <ToastContext.Provider value={value}>
      {children}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>');
  return ctx;
}
