import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  healingService: { pending: vi.fn(), approve: vi.fn(), reject: vi.fn() },
}));

import { healingService } from '../../../services/api';
import HealingQueuePanel from '../HealingQueuePanel';

const recovery = {
  id: 'rec-1', drift_event_id: 'd1', source_id: 'orders', status: 'pending',
  diagnosis: 'schema drift', generation_method: 'llm', validation_passed: true, post_kl_divergence: 0.01,
  shim_code: null, decided_by: null, decision_note: null, decided_at: null, created_at: '2026-01-01T00:00:00Z',
};

// BUG-253: Approve/Reject signed a WORM override on one click, without the confirm
// step the Cockpit's version of the same queue has.
describe('HealingQueuePanel decisions need a confirming click (BUG-253)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(healingService.pending).mockResolvedValue([recovery]);
    vi.mocked(healingService.approve).mockResolvedValue({ status: 'ok' });
    vi.mocked(healingService.reject).mockResolvedValue({ status: 'ok' });
  });

  it('approve: the first click arms, the second commits', async () => {
    const user = userEvent.setup();
    render(<HealingQueuePanel />);
    await user.click(await screen.findByRole('button', { name: 'Approve' }));
    expect(healingService.approve).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Confirm approve' }));
    await waitFor(() => expect(healingService.approve).toHaveBeenCalledWith('rec-1', 'workbench-operator', 'approved via workbench'));
  });

  it('reject can be cancelled without calling the service', async () => {
    const user = userEvent.setup();
    render(<HealingQueuePanel />);
    await user.click(await screen.findByRole('button', { name: 'Reject' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(healingService.reject).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Reject' }));
    await user.click(screen.getByRole('button', { name: 'Confirm reject' }));
    await waitFor(() => expect(healingService.reject).toHaveBeenCalledWith('rec-1', 'workbench-operator', 'rejected via workbench'));
  });
});
