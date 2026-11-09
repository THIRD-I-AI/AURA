import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  etlService: {
    listSourceFiles: vi.fn().mockResolvedValue([]),
    previewSource: vi.fn().mockResolvedValue(null),
    execute: vi.fn().mockResolvedValue({}),
    getDownloadUrl: vi.fn(),
  },
  uploadService: {
    getUploadedFiles: vi.fn().mockResolvedValue([]),
  },
  pipelineService: {
    list: vi.fn().mockResolvedValue({ status: 'success', pipelines: [] }),
    get: vi.fn(),
    generate: vi.fn(),
    executeAsync: vi.fn(),
    save: vi.fn(),
    remove: vi.fn(),
    getDownloadUrl: vi.fn(),
  },
}));

// Mock the PipelineMonitor sub-component. A test sets ``monitor.result`` to have the
// monitor report that run summary as its SSE 'complete' event.
const monitor = vi.hoisted(() => ({ result: null as Record<string, unknown> | null }));
vi.mock('../../components/PipelineMonitor', async () => {
  const { useEffect } = await import('react');
  const Monitor = ({ onComplete }: { onComplete?: (r: Record<string, unknown>) => void }) => {
    useEffect(() => { if (monitor.result) onComplete?.(monitor.result); }, [onComplete]);
    return <div data-testid="pipeline-monitor">Monitor</div>;
  };
  return { default: Monitor, PipelineMonitor: Monitor };
});

import PipelinesPanel from '../PipelinesPanel';
import { uploadService } from '../../services/api';
import { ToastProvider } from '../../contexts/ToastContext';

// PipelinesPanel reads useToast() (BUG-148: unified onto the shared queue),
// so it needs a real provider in the tree, not a bare render.
function renderPanel() {
  return render(<ToastProvider><PipelinesPanel /></ToastProvider>);
}

describe('PipelinesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the page heading', () => {
    renderPanel();
    expect(screen.getByText('Data Pipeline Builder')).toBeInTheDocument();
  });

  it('renders tab buttons', () => {
    renderPanel();
    expect(screen.getByText('AI Pipeline')).toBeInTheDocument();
    expect(screen.getByText('Visual Builder')).toBeInTheDocument();
    expect(screen.getByText('Saved')).toBeInTheDocument();
  });

  it('renders KPI cards', () => {
    renderPanel();
    expect(screen.getByText('Source Files')).toBeInTheDocument();
    expect(screen.getByText('Pipeline Steps')).toBeInTheDocument();
  });

  // BUG-251: the source-file list came from a bare fetch() with no Authorization
  // header, so a signed-in user's uploads never reached the dropdown.
  it('lists source files through the authenticated client, data files only', async () => {
    const bareFetch = vi.fn();
    globalThis.fetch = bareFetch as unknown as typeof fetch;
    vi.mocked(uploadService.getUploadedFiles).mockResolvedValue([
      { filename: 'orders.csv', size: 1, modified: '' },
      { filename: 'notes.txt', size: 1, modified: '' },
      { filename: 'events.parquet', size: 1, modified: '' },
    ]);
    renderPanel();
    await waitFor(() => expect(screen.getByRole('option', { name: 'orders.csv' })).toBeInTheDocument());
    expect(screen.getByRole('option', { name: 'events.parquet' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'notes.txt' })).not.toBeInTheDocument();
    expect(bareFetch).not.toHaveBeenCalled();
  });

  it('tells the user when the file list cannot be loaded', async () => {
    vi.mocked(uploadService.getUploadedFiles).mockRejectedValue(new Error('HTTP 500'));
    renderPanel();
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load your uploaded files');
  });
});

// BUG-395: a slower preview for the file selected first overwrote the preview of the
// file selected after it.
describe('PipelinesPanel source preview ordering (BUG-395)', () => {
  it('keeps the preview of the most recently selected file', async () => {
    const { etlService } = await import('../../services/api');
    const { fireEvent } = await import('@testing-library/react');
    (uploadService.getUploadedFiles as ReturnType<typeof vi.fn>).mockResolvedValue([
      { filename: 'first.csv' }, { filename: 'second.csv' },
    ]);
    let releaseFirst: (v: unknown) => void = () => {};
    (etlService.previewSource as ReturnType<typeof vi.fn>).mockImplementation((file: string) =>
      file === 'first.csv'
        ? new Promise(resolve => { releaseFirst = resolve; })
        : Promise.resolve({ table_name: 'second_table', columns: [{ name: 'b', type: 'INT' }], row_count: 2, preview: [] }),
    );

    renderPanel();
    fireEvent.click(screen.getByText('Visual Builder'));
    const select = await screen.findByDisplayValue('Select an uploaded file…');
    await screen.findByRole('option', { name: 'second.csv' });

    fireEvent.change(select, { target: { value: 'first.csv' } });
    fireEvent.change(select, { target: { value: 'second.csv' } });
    await screen.findByText('second_table');

    releaseFirst({ table_name: 'first_table', columns: [{ name: 'a', type: 'INT' }], row_count: 1, preview: [] });
    await new Promise(r => setTimeout(r, 20));

    expect(screen.queryByText('first_table')).not.toBeInTheDocument();
    expect(screen.getByText('second_table')).toBeInTheDocument();
  });
});

describe('PipelinesPanel Edit in Visual Builder (BUG-426)', () => {
  it('keeps the AI pipeline steps when its source file differs from the selected one', async () => {
    const { pipelineService } = await import('../../services/api');
    const { fireEvent } = await import('@testing-library/react');
    (uploadService.getUploadedFiles as ReturnType<typeof vi.fn>).mockResolvedValue([{ filename: 'products.csv' }]);
    (pipelineService.generate as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'success',
      pipeline: {
        name: 'Top products',
        source: { type: 'file', file_name: 'products.csv' },
        steps: [
          { id: 's1', type: 'filter', description: 'rating above 4', config: {} },
          { id: 's2', type: 'sort', description: 'by price', config: {} },
        ],
        sink: { format: 'csv' },
      },
    });

    renderPanel();
    fireEvent.change(await screen.findByPlaceholderText(/Read products\.csv/), { target: { value: 'top products' } });
    fireEvent.click(screen.getByText('Generate Pipeline'));
    fireEvent.click(await screen.findByText('Edit in Visual Builder'));

    expect(await screen.findByText('2 steps')).toBeInTheDocument();
  });
});

// ADR-002 phase 2b: a 'degraded' run wrote its sink but held rows aside or raised an
// alert. It was titled 'Pipeline Failed' (inviting a re-run that doubles an append
// sink), and an ETL run said 'Pipeline Complete' with no word of its quarantined rows.
describe('PipelinesPanel degraded runs (ADR-002 phase 2b)', () => {
  const heal = {
    sources: [], rows_quarantined: 3, repairs: 0, alerts: 1,
    quarantine_table: '_aura_quarantine_pipe_1',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    monitor.result = null;
  });

  it('shows a degraded pipeline run as complete, with its quarantined rows', async () => {
    const { pipelineService } = await import('../../services/api');
    const { fireEvent } = await import('@testing-library/react');
    (pipelineService.generate as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'success',
      pipeline: { name: 'p', source: { type: 'file', file_name: 'a.csv' }, steps: [], sink: { format: 'csv' } },
    });
    (pipelineService.executeAsync as ReturnType<typeof vi.fn>).mockResolvedValue({ status: 'success', run_id: 'run_1' });
    monitor.result = {
      run_id: 'run_1', pipeline_id: 'pipe_1', status: 'degraded', rows_read: 10, rows_written: 7,
      columns_in: ['v'], columns_out: ['v'], steps_executed: 0, steps_skipped: 0, duration_ms: 5, heal,
    };

    renderPanel();
    fireEvent.change(await screen.findByPlaceholderText(/Read products\.csv/), { target: { value: 'x' } });
    fireEvent.click(screen.getByText('Generate Pipeline'));
    fireEvent.click(await screen.findByText('Execute Pipeline'));

    expect(await screen.findByText('Pipeline Complete — Needs Review')).toBeInTheDocument();
    expect(screen.queryByText('Pipeline Failed')).not.toBeInTheDocument();
    const notice = screen.getByTestId('heal-notice');
    expect(notice).toHaveTextContent('3 rows could not be verified');
    expect(notice).toHaveTextContent('_aura_quarantine_pipe_1');
    expect(notice).toHaveTextContent('1 data-quality alert');
    expect(screen.getByText('Degraded')).toHaveClass('text-warn');
  });

  it('still titles a failed pipeline run as failed', async () => {
    const { pipelineService } = await import('../../services/api');
    const { fireEvent } = await import('@testing-library/react');
    (pipelineService.generate as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'success',
      pipeline: { name: 'p', source: { type: 'file', file_name: 'a.csv' }, steps: [], sink: { format: 'csv' } },
    });
    (pipelineService.executeAsync as ReturnType<typeof vi.fn>).mockResolvedValue({ status: 'success', run_id: 'run_2' });
    monitor.result = {
      run_id: 'run_2', pipeline_id: 'pipe_1', status: 'failed', rows_read: 0, rows_written: 0,
      columns_in: [], columns_out: [], steps_executed: 0, steps_skipped: 0, duration_ms: 5, error: 'boom',
    };

    renderPanel();
    fireEvent.change(await screen.findByPlaceholderText(/Read products\.csv/), { target: { value: 'x' } });
    fireEvent.click(screen.getByText('Generate Pipeline'));
    fireEvent.click(await screen.findByText('Execute Pipeline'));

    expect(await screen.findByText('Pipeline Failed')).toBeInTheDocument();
    expect(screen.queryByTestId('heal-notice')).not.toBeInTheDocument();
  });

  it('tells the user a degraded ETL run held rows in quarantine', async () => {
    const { etlService } = await import('../../services/api');
    const { fireEvent } = await import('@testing-library/react');
    (uploadService.getUploadedFiles as ReturnType<typeof vi.fn>).mockResolvedValue([{ filename: 'a.csv' }]);
    (etlService.previewSource as ReturnType<typeof vi.fn>).mockResolvedValue({
      table_name: 'a', columns: [{ name: 'v', type: 'INT' }], row_count: 10, preview: [],
    });
    (etlService.execute as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'degraded', pipeline_name: 'p',
      source: { file: 'a.csv', row_count: 10, columns: [{ name: 'v', type: 'INT' }] },
      output: { row_count: 7, columns: [{ name: 'v', type: 'INT' }], file: 'a.parquet', format: 'parquet' },
      transform_sql: 'SELECT 1', transforms_applied: 0, preview: [], execution_time_ms: 5,
      preview_only: false, heal: { ...heal, alerts: 0 },
    });

    renderPanel();
    fireEvent.click(screen.getByText('Visual Builder'));
    const select = await screen.findByDisplayValue('Select an uploaded file…');
    await screen.findByRole('option', { name: 'a.csv' });
    fireEvent.change(select, { target: { value: 'a.csv' } });
    fireEvent.click(await screen.findByText('Execute Pipeline'));

    expect(await screen.findByText('Pipeline Complete — Needs Review')).toBeInTheDocument();
    const notice = screen.getByTestId('heal-notice');
    expect(notice).toHaveTextContent('3 rows could not be verified');
    expect(notice).toHaveTextContent('_aura_quarantine_pipe_1');
    expect(notice).not.toHaveTextContent('alert');
  });
});
