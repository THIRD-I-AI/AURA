import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The inbound-hooks section has its own suite (InboundHooksSection.test.tsx).
vi.mock('../InboundHooksSection', () => ({ default: () => null }));
vi.mock('../../../services/api', () => ({
  webhookService: {
    list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(), test: vi.fn(), events: vi.fn(),
  },
}));

import { webhookService } from '../../../services/api';
import WebhooksPanel from '../WebhooksPanel';

const list = vi.mocked(webhookService.list);
const create = vi.mocked(webhookService.create);
const update = vi.mocked(webhookService.update);
const remove = vi.mocked(webhookService.remove);
const test = vi.mocked(webhookService.test);
const events = vi.mocked(webhookService.events);

const URL = 'https://example.com/hook';
const hook = { id: 'w1', url: URL, events: ['audit.sealed'], active: true, retries: 3 };
const listOf = (...hooks: unknown[]) => ({ status: 'success', webhooks: hooks }) as never;

// BUG-257: the panel could only list webhooks.
describe('WebhooksPanel: register, test, pause and delete (BUG-257)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    list.mockResolvedValue(listOf());
    events.mockResolvedValue({ status: 'success', events: ['audit.sealed', 'pipeline.completed'] });
  });

  it('registers a webhook for the picked events', async () => {
    create.mockResolvedValue({ status: 'success', webhook: hook } as never);
    const user = userEvent.setup();
    render(<WebhooksPanel />);
    await screen.findByText('No webhooks configured');

    await user.click(screen.getByRole('button', { name: /new webhook/i }));
    await user.type(screen.getByLabelText('Endpoint URL'), URL);
    await user.click(await screen.findByLabelText('pipeline.completed'));
    list.mockResolvedValue(listOf(hook));
    await user.click(screen.getByRole('button', { name: 'Register webhook' }));

    await waitFor(() => expect(create).toHaveBeenCalledWith({ url: URL, events: ['pipeline.completed'], description: undefined }));
    expect(await screen.findByText(URL)).toBeInTheDocument();
  });

  it('validates locally and shows the server reason when registration is refused', async () => {
    const user = userEvent.setup();
    render(<WebhooksPanel />);
    await screen.findByText('No webhooks configured');
    await user.click(screen.getByRole('button', { name: /new webhook/i }));
    const save = screen.getByRole('button', { name: 'Register webhook' });

    await user.type(screen.getByLabelText('Endpoint URL'), 'https://example.com/x');
    await user.click(save);
    expect(screen.getByRole('alert')).toHaveTextContent(/at least one event/);
    expect(create).not.toHaveBeenCalled();

    create.mockRejectedValue(new Error('url resolves to a private address'));
    await user.click(await screen.findByLabelText('audit.sealed'));
    await user.click(save);
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('url resolves to a private address'));
  });

  it('reports the real outcome of a test delivery', async () => {
    list.mockResolvedValue(listOf(hook));
    test.mockResolvedValueOnce({ status: 'success', delivery: { status: 'failed', http_status: 500, error: 'HTTP 500' } } as never)
      .mockResolvedValueOnce({ status: 'success', delivery: { status: 'success', http_status: 200, error: null } } as never);
    const user = userEvent.setup();
    render(<WebhooksPanel />);
    const button = await screen.findByRole('button', { name: `Send test event to ${URL}` });
    await user.click(button);
    expect(await screen.findByText(/test event to .* failed: HTTP 500/i)).toBeInTheDocument();
    await user.click(button);
    expect(await screen.findByText(/test event delivered .*HTTP 200/i)).toBeInTheDocument();
  });

  it('pauses through the update endpoint, and deletes only after a confirming click', async () => {
    list.mockResolvedValue(listOf(hook));
    update.mockResolvedValue({ status: 'success', webhook: { ...hook, active: false } } as never);
    remove.mockResolvedValue({ status: 'success' });
    const user = userEvent.setup();
    render(<WebhooksPanel />);
    await user.click(await screen.findByRole('button', { name: `Pause ${URL}` }));
    await waitFor(() => expect(update).toHaveBeenCalledWith('w1', { active: false }));

    await user.click(screen.getByRole('button', { name: `Delete ${URL}` }));
    expect(remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith('w1'));
  });
});
