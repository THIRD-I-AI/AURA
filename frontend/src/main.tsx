import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/design-system.css'
import './styles/components.css'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ui/ErrorBoundary'
import { ThemeProvider } from './contexts/ThemeContext'

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
        <App />
      </ThemeProvider>
    </ErrorBoundary>
  </StrictMode>,
)
