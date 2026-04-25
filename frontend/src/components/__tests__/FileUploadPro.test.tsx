import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  uploadService: { uploadFile: vi.fn() },
}));

// useSSE is the SSE pool hook; the upload component subscribes per-row.
// In tests we don't care about live progress events.
vi.mock('../../hooks/useSSE', () => ({
  useSSE: () => ({
    lastEvent: null,
    connected: false,
    error: false,
    retryCount: 0,
    disconnect: () => {},
    reconnect: () => {},
  }),
}));

import FileUpload from '../FileUploadPro';
import { uploadService } from '../../services/api';

describe('FileUploadPro', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('renders the dropzone with role=button and a descriptive aria-label', () => {
    render(<FileUpload />);
    const zone = screen.getByRole('button', { name: /upload data file/i });
    expect(zone).toBeInTheDocument();
    expect(zone).toHaveAttribute('tabIndex', '0');
  });

  it('opens the file picker when Enter is pressed on the dropzone', async () => {
    const user = userEvent.setup();
    render(<FileUpload />);
    const zone = screen.getByRole('button', { name: /upload data file/i });
    // The hidden input is what the dropzone clicks; spy on its click().
    const hiddenInput = zone.parentElement?.querySelector('input[type="file"]') as HTMLInputElement;
    expect(hiddenInput).toBeTruthy();
    const clickSpy = vi.spyOn(hiddenInput, 'click').mockImplementation(() => {});

    zone.focus();
    await user.keyboard('{Enter}');
    expect(clickSpy).toHaveBeenCalled();
  });

  it('uploads a selected file and shows it in the queue as completed', async () => {
    (uploadService.uploadFile as ReturnType<typeof vi.fn>).mockResolvedValue({
      file_id: 'AURA-TEST',
      filename: 'sample.csv',
      rows: 10,
      columns: ['a', 'b'],
    });

    render(<FileUpload />);
    const hiddenInput = screen
      .getByRole('button', { name: /upload data file/i })
      .parentElement?.querySelector('input[type="file"]') as HTMLInputElement;

    const file = new File(['a,b\n1,2\n3,4'], 'sample.csv', { type: 'text/csv' });
    const user = userEvent.setup();
    await user.upload(hiddenInput, file);

    await waitFor(() => expect(uploadService.uploadFile).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText(/uploaded successfully/i)).toBeInTheDocument(),
    );
    expect(screen.getByText('sample.csv')).toBeInTheDocument();
  });

  it('shows a Failed badge when uploadService rejects', async () => {
    (uploadService.uploadFile as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('Network error'),
    );

    render(<FileUpload />);
    const hiddenInput = screen
      .getByRole('button', { name: /upload data file/i })
      .parentElement?.querySelector('input[type="file"]') as HTMLInputElement;

    const file = new File(['a,b\n1,2'], 'broken.csv', { type: 'text/csv' });
    const user = userEvent.setup();
    await user.upload(hiddenInput, file);

    await waitFor(() => expect(screen.getByText(/failed/i)).toBeInTheDocument());
    expect(screen.getByText(/network error/i)).toBeInTheDocument();
  });
});
