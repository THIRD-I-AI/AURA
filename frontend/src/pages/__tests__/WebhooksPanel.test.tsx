import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../../hooks/useSSE', () => ({
  useSSE: vi.fn(),
}));

vi.mock('../../services/api', () => ({
  webhookService: {
    list: vi.fn().mockResolvedValue({ webhooks: [] }),
    deliveries: vi.fn().mockResolvedValue({ deliveries: [] }),
    events: vi.fn().mockResolvedValue({ events: [] }),
  },
  inboundHookService: {
    list: vi.fn().mockResolvedValue({ hooks: [] }),
  },
  pipelineService: {
    list: vi.fn().mockResolvedValue({ pipelines: [] }),
  },
}));

import WebhooksPanel from '../WebhooksPanel';

describe('WebhooksPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders tab buttons', () => {
    render(<WebhooksPanel />);
    expect(screen.getByText(/^Outbound/)).toBeInTheDocument();
    expect(screen.getByText(/^Inbound/)).toBeInTheDocument();
    expect(screen.getByText(/^Deliveries/)).toBeInTheDocument();
  });

  it('renders refresh button', () => {
    render(<WebhooksPanel />);
    // The button text includes the ↻ symbol
    expect(screen.getByText(/Refresh/)).toBeInTheDocument();
  });

  it('renders outbound section by default', () => {
    render(<WebhooksPanel />);
    expect(screen.getByText('Register outbound webhook')).toBeInTheDocument();
  });

  it('switches tabs on click', async () => {
    const user = userEvent.setup();
    render(<WebhooksPanel />);

    await user.click(screen.getByText(/^Inbound/));
    // After clicking Inbound tab, inbound form content should be visible
    expect(screen.getByText('Register inbound hook')).toBeInTheDocument();
  });
});
