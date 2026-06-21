import React from 'react';

interface Props { panelTitle: string; children: React.ReactNode }
interface State { error: Error | null }

export class PanelErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    // Contained on purpose: log, but never rethrow — the workspace survives.
    console.error(`[terminal] panel "${this.props.panelTitle}" crashed:`, error);
  }

  private reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return (
        <div data-testid="panel-error" className="panel-error">
          <strong>{this.props.panelTitle} failed</strong>
          <p>{this.state.error.message}</p>
          <button onClick={this.reset}>Reload panel</button>
        </div>
      );
    }
    return this.props.children;
  }
}
