import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { chatService } from '../../../services/api';
import { AskAuraChat } from '../AskAuraChat';

/**
 * BUG-133: Commander (POST /chat/stream) is off by default
 * (AURA_COMMANDER_ENABLED=false in aurabackend/shared/config.py) and 404s.
 * streamMessage's own docstring says callers should fall back to
 * sendMessage (POST /chat, always live) on that case, but the only caller
 * — this panel, which is the app's default landing view (Workbench.tsx
 * defaults nav to 'Cockpit') — never did. Every query rendered a static
 * "Commander offline" message with no SQL, no execution, no answer, in
 * the default configuration.
 */
async function ask(question = 'What is the total revenue?') {
  const user = userEvent.setup();
  render(<AskAuraChat pushFeed={vi.fn()} setHistory={vi.fn()} />);
  await user.type(screen.getByPlaceholderText(/ask anything about your data/i), question);
  await user.click(screen.getByRole('button', { name: /ask/i }));
}

describe('AskAuraChat commander fallback', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('falls back to the gateway and shows a real answer when commander is disabled', async () => {
    vi.spyOn(chatService, 'streamMessage').mockRejectedValue(new Error('commander_disabled'));
    vi.spyOn(chatService, 'sendMessage').mockResolvedValue({
      job_id: 'j1',
      status: 'Success',
      final_query: 'SELECT SUM(revenue) FROM sales',
      execution_result: {
        success: true,
        columns: ['total'],
        rows: [[42]],
        row_count: 1,
        conclusion: 'Total revenue is 42.',
      },
    } as never);

    await ask();

    await waitFor(() => {
      expect(screen.getByText('Total revenue is 42.')).toBeInTheDocument();
    });
    expect(screen.getByText(/SELECT SUM\(revenue\)/)).toBeInTheDocument();
    expect(screen.queryByText(/commander offline/i)).not.toBeInTheDocument();
  });

  it('shows a real error, not a fake offline message, when the fallback query itself fails', async () => {
    vi.spyOn(chatService, 'streamMessage').mockRejectedValue(new Error('commander_disabled'));
    vi.spyOn(chatService, 'sendMessage').mockResolvedValue({
      job_id: 'j2',
      status: 'Error',
      error_message: 'SQL generation failed: table not found',
    } as never);

    await ask();

    await waitFor(() => {
      expect(screen.getByText(/table not found/i)).toBeInTheDocument();
    });
  });

  it('renders the chart from a real chart_spec on the fallback path (BUG-134)', async () => {
    if (typeof ResizeObserver === 'undefined') {
      (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
      };
    }
    vi.spyOn(chatService, 'streamMessage').mockRejectedValue(new Error('commander_disabled'));
    vi.spyOn(chatService, 'sendMessage').mockResolvedValue({
      job_id: 'j3',
      status: 'Success',
      final_query: 'SELECT region, revenue FROM sales',
      execution_result: {
        success: true,
        columns: ['region', 'revenue'],
        rows: [['east', 10], ['west', 20]],
        data: [{ region: 'east', revenue: 10 }, { region: 'west', revenue: 20 }],
        row_count: 2,
        chart_spec: { type: 'bar', x: 'region', y: 'revenue', title: 'Revenue by region' },
      },
    } as never);

    await ask();

    await waitFor(() => {
      expect(screen.getByText(/revenue by region/i)).toBeInTheDocument();
    });
  });

  it('still shows the offline message for a genuine server error unrelated to commander being disabled', async () => {
    vi.spyOn(chatService, 'streamMessage').mockRejectedValue(new Error('stream failed: 503'));
    vi.spyOn(chatService, 'sendMessage');

    await ask();

    await waitFor(() => {
      expect(screen.getByText(/temporarily unavailable/i)).toBeInTheDocument();
    });
    expect(chatService.sendMessage).not.toHaveBeenCalled();
  });
});
