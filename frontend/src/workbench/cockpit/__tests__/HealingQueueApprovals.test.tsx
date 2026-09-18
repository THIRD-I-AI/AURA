import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HealingQueueApprovals } from '../HealingQueueApprovals';
import type { Heal } from '../types';

const PENDING: Heal = {
  id: 'h1',
  title: 'drift-fix-42',
  method: 'schema-shim',
  safe: true,
  sub: 'column type mismatch on ingest',
  state: 'pending',
};

// BUG-106: Approve/Reject used to be plain <div onClick> with no role,
// tabIndex, or keyboard handler — unreachable by keyboard/screen-reader
// users for a safety-critical HITL approval gate on autonomous deploys.
describe('HealingQueueApprovals', () => {
  it('renders Approve/Reject as real, keyboard-reachable buttons', () => {
    render(<HealingQueueApprovals healing={[PENDING]} pendingCount={1} decideHeal={vi.fn()} />);

    const approve = screen.getByRole('button', { name: /approve & deploy/i });
    const reject = screen.getByRole('button', { name: /reject/i });

    expect(approve.tagName).toBe('BUTTON');
    expect(reject.tagName).toBe('BUTTON');
    // Real <button> elements are focusable/keyboard-activatable by default —
    // no manual tabIndex/role/onKeyDown patchwork needed, unlike a <div>.
    expect(approve).not.toHaveAttribute('tabindex', '-1');
  });

  it('clicking Approve & deploy calls decideHeal(id, true)', () => {
    const decideHeal = vi.fn();
    render(<HealingQueueApprovals healing={[PENDING]} pendingCount={1} decideHeal={decideHeal} />);

    fireEvent.click(screen.getByRole('button', { name: /approve & deploy/i }));

    expect(decideHeal).toHaveBeenCalledWith('h1', true);
  });

  it('clicking Reject calls decideHeal(id, false)', () => {
    const decideHeal = vi.fn();
    render(<HealingQueueApprovals healing={[PENDING]} pendingCount={1} decideHeal={decideHeal} />);

    fireEvent.click(screen.getByRole('button', { name: /reject/i }));

    expect(decideHeal).toHaveBeenCalledWith('h1', false);
  });
});
