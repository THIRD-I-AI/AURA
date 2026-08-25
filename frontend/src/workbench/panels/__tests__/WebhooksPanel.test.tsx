import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  webhookService: {
    list: vi.fn(),
  },
}));

import { webhookService } from '../../../services/api';
import WebhooksPanel from '../WebhooksPanel';

const list = webhookService.list as ReturnType<typeof vi.fn>;

const webhooks = [
  { id: 'h1', url: 'https://example.com/hooks/audit', events: ['audit.sealed'], active: true, retries: 3 },
  { id: 'h2', url: 'https://example.com/hooks/drift', events: ['drift.healed'], active: false, retries: 0 },
];

describe('WebhooksPanel', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders real webhook rows in the DataTable', async () => {
    list.mockResolvedValue({ webhooks });
    render(<WebhooksPanel />);
    await waitFor(() => expect(screen.getByText('https://example.com/hooks/audit')).toBeInTheDocument());
    expect(screen.getByText('https://example.com/hooks/drift')).toBeInTheDocument();
    expect(screen.getByText('2 outbound webhooks · HMAC-signed')).toBeInTheDocument();
  });

  it('renders an honest empty state when there are no webhooks', async () => {
    list.mockResolvedValue({ webhooks: [] });
    render(<WebhooksPanel />);
    await waitFor(() => expect(screen.getByText('No webhooks configured')).toBeInTheDocument());
  });

  it('renders an honest error state when the gateway is unreachable', async () => {
    list.mockRejectedValue(new Error('network down'));
    render(<WebhooksPanel />);
    await waitFor(() => expect(screen.getByText(/could not reach the gateway to list webhooks/i)).toBeInTheDocument());
  });

  it('filters rows by url text', async () => {
    list.mockResolvedValue({ webhooks });
    render(<WebhooksPanel />);
    await waitFor(() => expect(screen.getByText('https://example.com/hooks/audit')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('Filter webhooks…'), 'drift');

    expect(screen.queryByText('https://example.com/hooks/audit')).not.toBeInTheDocument();
    expect(screen.getByText('https://example.com/hooks/drift')).toBeInTheDocument();
  });

  it('sorts by status ascending/descending on header click', async () => {
    list.mockResolvedValue({ webhooks });
    render(<WebhooksPanel />);
    await waitFor(() => expect(screen.getByText('https://example.com/hooks/audit')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^status/i }));

    const rowsInOrder = screen.getAllByRole('row').slice(1); // drop header row
    // ascending: paused (0) sorts before active (1)
    expect(rowsInOrder[0]).toHaveTextContent('PAUSED');
  });
});
