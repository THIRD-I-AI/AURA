import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  savedQueryService: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() },
}));

import { savedQueryService } from '../../../services/api';
import LibraryPanel from '../LibraryPanel';

const list = vi.mocked(savedQueryService.list);
const create = vi.mocked(savedQueryService.create);
const update = vi.mocked(savedQueryService.update);
const remove = vi.mocked(savedQueryService.remove);

const saved = { id: 'q1', name: 'Monthly revenue', sql: 'SELECT 1', starred: false, created_at: '', updated_at: '' };

// BUG-257: nothing in the app could create a saved query, so the Library,
// Scheduler and Lineage views stayed empty forever.
describe('LibraryPanel: create, star and delete saved queries (BUG-257)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    list.mockResolvedValue([]);
  });

  it('saves a new query and reloads the list', async () => {
    create.mockResolvedValue(saved);
    const user = userEvent.setup();
    render(<LibraryPanel />);
    await screen.findByText('No saved queries yet');

    await user.click(screen.getByRole('button', { name: /new query/i }));
    await user.type(screen.getByLabelText('Name'), '  Monthly revenue ');
    await user.type(screen.getByLabelText('SQL'), 'SELECT 1');
    list.mockResolvedValue([saved]);
    await user.click(screen.getByRole('button', { name: 'Save query' }));

    await waitFor(() => expect(create).toHaveBeenCalledWith({ name: 'Monthly revenue', sql: 'SELECT 1' }));
    expect(await screen.findByText('Monthly revenue')).toBeInTheDocument();
    expect(screen.queryByTestId('wb-library-new')).not.toBeInTheDocument();
  });

  it('requires both fields locally, and shows the server error when the save fails', async () => {
    const user = userEvent.setup();
    render(<LibraryPanel />);
    await screen.findByText('No saved queries yet');
    await user.click(screen.getByRole('button', { name: /new query/i }));

    await user.click(screen.getByRole('button', { name: 'Save query' }));
    expect(screen.getByRole('alert')).toHaveTextContent(/both required/);
    expect(create).not.toHaveBeenCalled();

    create.mockRejectedValue(new Error('sql is required'));
    await user.type(screen.getByLabelText('Name'), 'x');
    await user.type(screen.getByLabelText('SQL'), 'y');
    await user.click(screen.getByRole('button', { name: 'Save query' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('sql is required'));
    expect(screen.getByTestId('wb-library-new')).toBeInTheDocument(); // the draft is kept
  });

  it('toggles the star through the real update endpoint', async () => {
    list.mockResolvedValue([saved]);
    update.mockResolvedValue({ ...saved, starred: true });
    const user = userEvent.setup();
    render(<LibraryPanel />);
    await user.click(await screen.findByRole('button', { name: 'Star Monthly revenue' }));
    await waitFor(() => expect(update).toHaveBeenCalledWith('q1', { starred: true }));
  });

  it('deletes only after a confirming click', async () => {
    list.mockResolvedValue([saved]);
    remove.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<LibraryPanel />);
    await user.click(await screen.findByRole('button', { name: 'Delete Monthly revenue' }));
    expect(remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Delete Monthly revenue' }));
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith('q1'));
  });
});
