import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { chatService } from '../../../services/api';
import AskAuraPanel from '../AskAuraPanel';

/**
 * The panel must show the reason a question failed, not a generic stand-in.
 *
 * A failure BEFORE execution (SQL generation, planning) arrives on the
 * top-level error_message; only execution failures populate
 * execution_result.error. The panel read execution_result.error alone, so
 * every pre-execution failure rendered as "Query failed." — a provider
 * daily-quota 429 looked exactly like broken SQL, and the gateway's own
 * humanized text ("rate-limited, try again shortly") was thrown away.
 *
 * Caught on the live deployment: Groq hit its 100k tokens-per-day cap and the
 * UI said only "Query failed."
 */
async function ask(question = 'What is the total revenue?') {
  const user = userEvent.setup();
  render(<AskAuraPanel />);
  await user.type(screen.getByRole('textbox', { name: 'Ask AURA' }), question);
  await user.click(screen.getByRole('button', { name: /ask/i }));
}

describe('AskAuraPanel error reporting', () => {
  beforeAll(() => {
    // The panel scrolls its transcript to the bottom after each answer.
    // jsdom implements no layout and so ships no Element.scrollTo, which
    // surfaces as an unhandled TypeError inside requestAnimationFrame —
    // failing the run even while every assertion passes. Stub it here rather
    // than defending against it in the component: real browsers have it.
    Element.prototype.scrollTo = vi.fn();
  });

  afterEach(() => { vi.restoreAllMocks(); });

  it('surfaces a pre-execution failure from error_message', async () => {
    vi.spyOn(chatService, 'sendMessage').mockResolvedValue({
      status: 'Error',
      error_message: 'SQL generation failed: The AI model is rate-limited. Try again shortly.',
    } as never);

    await ask();

    await waitFor(() => {
      expect(screen.getByText(/rate-limited/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('Query failed.')).not.toBeInTheDocument();
  });

  it('prefers execution_result.error when the failure is in execution', async () => {
    vi.spyOn(chatService, 'sendMessage').mockResolvedValue({
      status: 'Error',
      error_message: 'execution failed: generic wrapper',
      execution_result: {
        success: false,
        error: 'Execution failed: Catalog Error: Table "custmer" does not exist',
      },
    } as never);

    await ask();

    // The actionable DB error wins over the outer wrapper — a typo'd table
    // name is something the user can fix, so it must not be replaced.
    await waitFor(() => {
      expect(screen.getByText(/custmer/)).toBeInTheDocument();
    });
  });

  it('falls back to a generic message only when the server explains nothing', async () => {
    vi.spyOn(chatService, 'sendMessage').mockResolvedValue({
      status: 'Error',
    } as never);

    await ask();

    await waitFor(() => {
      expect(screen.getByText('Query failed.')).toBeInTheDocument();
    });
  });
});
