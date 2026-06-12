import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/600.css'
import './styles/design-system.css'
import './styles/components.css'
import './styles/tokens.css'
import './ui/primitives.css'
import './index.css'
import { BrowserRouter } from 'react-router-dom'
import { AppRoutes } from './AppRoutes'
import { ErrorBoundary } from './components/ui/ErrorBoundary'
import { ThemeProvider } from './contexts/ThemeContext'
import { AuthProvider } from './auth/AuthContext'

// Sentry: opt-in via VITE_SENTRY_DSN. Dynamic import keeps the SDK out of
// the main bundle when no DSN is configured.
const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN as string | undefined;
if (SENTRY_DSN) {
  import('@sentry/react').then((Sentry) => {
    Sentry.init({
      dsn: SENTRY_DSN,
      environment: (import.meta.env.VITE_SENTRY_ENV as string) || import.meta.env.MODE,
      tracesSampleRate: Number(import.meta.env.VITE_SENTRY_TRACES_RATE ?? '0'),
    });
  }).catch((err) => {
    console.warn('[sentry] failed to initialize:', err);
  });
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary
      resetLabel="Reload app"
      onError={(err, info) => {
        // Route to a structured logger once one exists; for now console is
        // fine — this line is the dividing wall between "one component crashes"
        // and "entire SPA goes blank".
        console.error('[App] top-level crash:', err, info?.componentStack);
      }}
    >
      <ThemeProvider>
        <BrowserRouter>
          <AuthProvider>
            <AppRoutes />
          </AuthProvider>
        </BrowserRouter>
      </ThemeProvider>
    </ErrorBoundary>
  </StrictMode>,
)
