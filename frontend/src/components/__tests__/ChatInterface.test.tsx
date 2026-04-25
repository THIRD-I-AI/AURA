import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Mock service modules BEFORE importing the component (Vitest hoists vi.mock).
vi.mock('../../services/api', () => ({
  chatService: { sendMessage: vi.fn() },
  executionService: { executeSql: vi.fn() },
  analyticsService: { saveQueryRecord: vi.fn().mockResolvedValue({}) },
  uploadService: { getUploadedFiles: vi.fn().mockResolvedValue([]) },
}));

// Recharts pulls in DOM-heavy code; stub the visualization to keep tests fast.
vi.mock('../RechartsVisualization', () => ({
  default: () => <div data-testid="viz" />,
}));

import { ChatInterface } from '../ChatInterface';
import { chatService } from '../../services/api';

describe('ChatInterface', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('renders the input with the documented aria-label', () => {
    render(<ChatInterface />);
    expect(screen.getByRole('textbox', { name: /ask about your data/i })).toBeInTheDocument();
  });

  it('exposes suggestions as a listbox with selectable options', () => {
    render(<ChatInterface />);
    const list = screen.getByRole('listbox', { name: /suggested questions/i });
    expect(list).toBeInTheDocument();
    const options = screen.getAllByRole('option');
    expect(options.length).toBeGreaterThan(0);
  });

  it('appends a user message and sends through chatService when the form is submitted', async () => {
    const user = userEvent.setup();
    (chatService.sendMessage as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'Success',
      job_id: 'job_test',
      final_query: 'SELECT 1',
      execution_time_ms: 5,
      execution_result: { success: true, data: [{ a: 1 }], columns: ['a'], rows: [[1]], row_count: 1 },
    });

    render(<ChatInterface />);
    const input = screen.getByRole('textbox', { name: /ask about your data/i });
    await user.type(input, 'show top products');
    await user.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() =>
      expect(chatService.sendMessage).toHaveBeenCalledWith(
        'show top products',
        expect.objectContaining({ sessionId: expect.any(String) }),
      ),
    );
    expect(screen.getByText('show top products')).toBeInTheDocument();
  });

  it('renders an error message when the chat service rejects', async () => {
    const user = userEvent.setup();
    (chatService.sendMessage as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('network down'),
    );

    render(<ChatInterface />);
    await user.type(
      screen.getByRole('textbox', { name: /ask about your data/i }),
      'hello',
    );
    await user.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() =>
      expect(screen.getByText(/error: network down/i)).toBeInTheDocument(),
    );
  });

  it('clicking a suggestion populates the input', async () => {
    const user = userEvent.setup();
    render(<ChatInterface />);
    const firstSuggestion = screen.getAllByRole('option')[0];
    await user.click(firstSuggestion);
    const input = screen.getByRole('textbox', { name: /ask about your data/i });
    expect((input as HTMLInputElement).value).toBe(firstSuggestion.textContent);
  });
});
