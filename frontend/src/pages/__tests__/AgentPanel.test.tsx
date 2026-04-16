import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../../hooks/useAgentExecutor', () => ({
  useAgentExecutor: vi.fn(() => [{
    phase: 'idle' as const,
    plan: null,
    report: null,
    progress: [],
    error: null,
  }, {
    submit: vi.fn(),
    cancel: vi.fn(),
  }]),
}));

import AgentPanel from '../AgentPanel';

describe('AgentPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the prompt textarea', () => {
    render(<AgentPanel />);
    expect(screen.getByPlaceholderText(/describe.*task/i)).toBeInTheDocument();
  });

  it('renders suggestion chips', () => {
    render(<AgentPanel />);
    // There should be at least one suggestion chip
    const buttons = screen.getAllByRole('button');
    expect(buttons.length).toBeGreaterThan(1);
  });

  it('renders the Run button', () => {
    render(<AgentPanel />);
    expect(screen.getByText(/run/i)).toBeInTheDocument();
  });

  it('does not show phase badge in idle state', () => {
    render(<AgentPanel />);
    // In idle state, phase badge should not be visible
    expect(screen.queryByText('Planning')).not.toBeInTheDocument();
    expect(screen.queryByText('Executing')).not.toBeInTheDocument();
  });
});
