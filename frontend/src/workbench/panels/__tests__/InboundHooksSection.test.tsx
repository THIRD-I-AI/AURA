import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  inboundHookService: {
    list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(),
    fireUrl: (slug: string) => `https://aura.test/api/v1/hooks/fire/${slug}`,
  },
  pipelineService: { list: vi.fn() },
}));

import { inboundHookService, pipelineService } from '../../../services/api';
import InboundHooksSection from '../InboundHooksSection';

const list = vi.mocked(inboundHookService.list);
const create = vi.mocked(inboundHookService.create);
const update = vi.mocked(inboundHookService.update);
const remove = vi.mocked(inboundHookService.remove);
const listPipelines = vi.mocked(pipelineService.list);

const hook = {
  id: 'h1', slug: 'orders-arrived', kind: 'pipeline' as const, target: 'pipe_1', active: true, description: 'from the ERP',
  pass_payload_as: null, last_fired_at: null, fire_count: 4, created_at: '', has_secret: false,
};
const hooksOf = (...hooks: unknown[]) => ({ status: 'success', hooks }) as never;

// BUG-257: the inbound-hook API had no UI at all.
describe('InboundHooksSection (BUG-257)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    list.mockResolvedValue(hooksOf());
    listPipelines.mockResolvedValue({ status: 'success', count: 1, pipelines: [{ id: 'pipe_1', name: 'Nightly orders' }] } as never);
  });

  it('lists hooks and warns when one has no signing secret', async () => {
    list.mockResolvedValue(hooksOf(hook));
    render(<InboundHooksSection />);
    expect(await screen.findByText('orders-arrived')).toBeInTheDocument();
    expect(screen.getByText('pipeline · pipe_1')).toBeInTheDocument();
    expect(screen.getByText('none — open URL')).toBeInTheDocument();
  });

  it('creates a pipeline hook from a picked pipeline, and shows the URL it will get', async () => {
    create.mockResolvedValue({ status: 'success', hook } as never);
    const user = userEvent.setup();
    render(<InboundHooksSection />);
    await screen.findByText('No inbound hooks');

    await user.click(screen.getByRole('button', { name: /new hook/i }));
    await user.type(screen.getByLabelText('Slug'), 'orders-arrived');
    expect(screen.getByText('POST https://aura.test/api/v1/hooks/fire/orders-arrived')).toBeInTheDocument();
    await user.selectOptions(await screen.findByLabelText('Pipeline'), 'pipe_1');
    await user.type(screen.getByLabelText(/signing secret/i), 's3cret');
    list.mockResolvedValue(hooksOf({ ...hook, has_secret: true }));
    await user.click(screen.getByRole('button', { name: 'Create hook' }));

    await waitFor(() => expect(create).toHaveBeenCalledWith({
      slug: 'orders-arrived', kind: 'pipeline', target: 'pipe_1', secret: 's3cret', description: undefined,
    }));
    expect(await screen.findByText('required')).toBeInTheDocument();
  });

  it('rejects a slug that is not URL-safe and a missing target before calling the server', async () => {
    const user = userEvent.setup();
    render(<InboundHooksSection />);
    await screen.findByText('No inbound hooks');
    await user.click(screen.getByRole('button', { name: /new hook/i }));

    await user.type(screen.getByLabelText('Slug'), 'Bad Slug!');
    await user.click(screen.getByRole('button', { name: 'Create hook' }));
    expect(screen.getByRole('alert')).toHaveTextContent(/lowercase letters/);

    await user.clear(screen.getByLabelText('Slug'));
    await user.type(screen.getByLabelText('Slug'), 'ok-slug');
    await user.selectOptions(screen.getByLabelText('Starts'), 'agent');
    await user.click(screen.getByRole('button', { name: 'Create hook' }));
    expect(screen.getByRole('alert')).toHaveTextContent(/what the agent should do/);
    expect(create).not.toHaveBeenCalled();
  });

  it('shows the server reason when the hook is refused', async () => {
    create.mockRejectedValue(new Error("slug 'orders-arrived' already registered"));
    const user = userEvent.setup();
    render(<InboundHooksSection />);
    await screen.findByText('No inbound hooks');
    await user.click(screen.getByRole('button', { name: /new hook/i }));
    await user.type(screen.getByLabelText('Slug'), 'orders-arrived');
    await user.selectOptions(await screen.findByLabelText('Pipeline'), 'pipe_1');
    await user.click(screen.getByRole('button', { name: 'Create hook' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('already registered'));
  });

  it('pauses through the update endpoint, and deletes only after a confirming click', async () => {
    list.mockResolvedValue(hooksOf(hook));
    update.mockResolvedValue({ status: 'success', hook: { ...hook, active: false } } as never);
    remove.mockResolvedValue({ status: 'success' });
    const user = userEvent.setup();
    render(<InboundHooksSection />);
    await user.click(await screen.findByRole('button', { name: 'Pause orders-arrived' }));
    await waitFor(() => expect(update).toHaveBeenCalledWith('h1', { active: false }));

    await user.click(screen.getByRole('button', { name: 'Delete orders-arrived' }));
    expect(remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith('h1'));
  });

  it('a failed load is an error with Retry, not an empty list', async () => {
    list.mockRejectedValue(new Error('503'));
    render(<InboundHooksSection />);
    expect(await screen.findByText(/could not reach the gateway to list inbound hooks/i)).toBeInTheDocument();
    expect(screen.queryByText('No inbound hooks')).not.toBeInTheDocument();
  });
});
