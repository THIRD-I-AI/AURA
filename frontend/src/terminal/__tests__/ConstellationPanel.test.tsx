import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

// jsdom can't lay out React Flow — mock it, capturing the props the panel passes
// so we can verify the computed graph + invoke onNodeClick (the cross-filter).
let captured: { nodes?: any[]; edges?: any[]; onNodeClick?: (e: unknown, n: unknown) => void } = {};
vi.mock('@xyflow/react', () => ({
  ReactFlow: (props: any) => {
    captured = props;
    return (
      <div data-testid="rf-mock">
        {(props.nodes ?? []).map((n: any) => (
          <span key={n.id}>{n.data.label}</span>
        ))}
      </div>
    );
  },
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Background: () => null,
  MiniMap: () => null,
  Controls: () => null,
  Handle: () => null,
  Position: { Top: 'top', Bottom: 'bottom' },
  useReactFlow: () => ({ setCenter: vi.fn() }),
}));

const lineageGet = vi.fn();
const getUploadedFiles = vi.fn();
vi.mock('../../services/api', () => ({
  lineageService: { get: () => lineageGet() },
  uploadService: { getUploadedFiles: () => getUploadedFiles() },
}));
const setActiveDataset = vi.fn();
vi.mock('../CockpitProvider', () => ({ useCockpit: () => ({ activeDataset: null, setActiveDataset }) }));

import ConstellationPanel from '../panels/ConstellationPanel';

const props = { api: {}, params: {}, containerApi: {} } as any;

const GRAPH = {
  success: true,
  nodes: [
    { id: 't1', type: 'table', label: 'sales', metadata: {} },
    { id: 'q1', type: 'saved_query', label: 'rev_by_region', metadata: {} },
  ],
  edges: [{ id: 'e1', source: 't1', target: 'q1' }],
  summary: { tables: 1, queries: 1, dashboards: 0, edges: 1 },
};
const EMPTY = { success: true, nodes: [], edges: [], summary: { tables: 0, queries: 0, dashboards: 0, edges: 0 } };

afterEach(() => {
  lineageGet.mockReset();
  getUploadedFiles.mockReset();
  setActiveDataset.mockReset();
});

describe('ConstellationPanel', () => {
  it('loads lineage, renders the graph, and cross-filters on a dataset-node click', async () => {
    captured = {};
    lineageGet.mockResolvedValue(GRAPH);
    getUploadedFiles.mockResolvedValue([]);
    render(<ConstellationPanel {...props} />);
    await waitFor(() => expect(screen.getByTestId('rf-mock')).toBeInTheDocument());
    expect(screen.getByText('sales')).toBeInTheDocument();
    expect(captured.nodes).toHaveLength(2);
    expect(captured.edges).toHaveLength(1);

    // clicking a TABLE node cross-filters the cockpit; a non-table node does not
    captured.onNodeClick?.({}, { id: 't1', data: { label: 'sales', kind: 'table' } });
    expect(setActiveDataset).toHaveBeenCalledWith('sales');
    setActiveDataset.mockClear();
    captured.onNodeClick?.({}, { id: 'q1', data: { label: 'rev_by_region', kind: 'saved_query' } });
    expect(setActiveDataset).not.toHaveBeenCalled();
  });

  it('merges uploaded datasets in as table nodes (deduped against lineage by stem)', async () => {
    captured = {};
    lineageGet.mockResolvedValue(GRAPH); // already has table "sales"
    getUploadedFiles.mockResolvedValue([
      { filename: 'sales.csv', size: 1 },    // dedup vs lineage "sales"
      { filename: 'customer.csv', size: 2 }, // genuinely new dataset node
    ]);
    render(<ConstellationPanel {...props} />);
    await waitFor(() => expect(screen.getByText('customer.csv')).toBeInTheDocument());
    // 2 lineage nodes + 1 new dataset (sales.csv deduped away) = 3
    expect(captured.nodes).toHaveLength(3);
  });

  it('shows an empty state when there is no lineage or datasets', async () => {
    lineageGet.mockResolvedValue(EMPTY);
    getUploadedFiles.mockResolvedValue([]);
    render(<ConstellationPanel {...props} />);
    await waitFor(() => expect(screen.getByText(/No datasets yet/i)).toBeInTheDocument());
  });

  it('shows an error state when lineage fails to load', async () => {
    lineageGet.mockRejectedValue(new Error('boom'));
    getUploadedFiles.mockResolvedValue([]);
    render(<ConstellationPanel {...props} />);
    await waitFor(() => expect(screen.getByText(/boom/)).toBeInTheDocument());
  });
});
