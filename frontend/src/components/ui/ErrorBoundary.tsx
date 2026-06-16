/**
 * Error Boundary
 * ===============
 * Class-based error boundary that catches render-phase errors.
 * Provides a fallback UI and optional retry button.
 *
 * Usage:
 *   <ErrorBoundary>
 *     <MyComponent />
 *   </ErrorBoundary>
 *
 *   <ErrorBoundary fallback={<CustomError />} onError={logError}>
 *     <MyComponent />
 *   </ErrorBoundary>
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, info: ErrorInfo) => void;
  /** Label shown on the reset button */
  resetLabel?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ErrorBoundary] caught:', error, info.componentStack);
    this.props.onError?.(error, info);
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children;

    if (this.props.fallback) return this.props.fallback;

    return (
      <div
        role="alert"
        style={{
          padding: 'var(--space-8)',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--color-error-200)',
          background: 'var(--color-error-50)',
          textAlign: 'center',
          color: 'var(--text-primary)',
        }}
      >
        <div
          style={{
            fontSize: '2.5rem',
            marginBottom: 'var(--space-3)',
          }}
        >
          ⚠
        </div>
        <h3
          style={{
            margin: '0 0 var(--space-2)',
            fontSize: 'var(--font-lg)',
            fontWeight: 'var(--weight-semibold)',
            color: 'var(--color-error-700)',
          }}
        >
          Something went wrong
        </h3>
        <p
          style={{
            margin: '0 0 var(--space-4)',
            fontSize: 'var(--font-sm)',
            color: 'var(--text-secondary)',
          }}
        >
          {this.state.error?.message ?? 'An unexpected error occurred.'}
        </p>
        <button
          onClick={this.handleReset}
          style={{
            padding: 'var(--space-2) var(--space-5)',
            borderRadius: 'var(--radius-md)',
            border: 'none',
            background: 'var(--color-error-600)',
            color: '#fff',
            fontSize: 'var(--font-sm)',
            fontWeight: 'var(--weight-medium)',
            cursor: 'pointer',
          }}
        >
          {this.props.resetLabel ?? 'Try again'}
        </button>
      </div>
    );
  }
}

export default ErrorBoundary;
