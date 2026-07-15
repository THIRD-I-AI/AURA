/* Workbench (Claude Design port) — login → boot → cockpit flow + panel smoke.
   Panels carry NO dummy data: everything comes from (mocked) real services. */
import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../services/api', () => ({
  API_BASE_URL: 'http://test/api/v1',
  chatService: { streamMessage: vi.fn().mockResolvedValue(undefined) },
  authService: { login: vi.fn() },
  getAuthToken: () => 'test-token',
  getCurrentWorkspaceId: () => 'default',
  analyticsService: {
    getQueryHistory: vi.fn().mockResolvedValue([
      { timestamp: '2026-07-02T09:41:00', query: 'SELECT 1', engine: 'DuckDB', status: 'completed' },
    ]),
  },
  healingService: {
    pending: vi.fn().mockResolvedValue([
      { id: 'rec-1', drift_event_id: 'drift-9', source_id: 'orders.customer_id', status: 'pending',
        diagnosis: 'schema rename drift', generation_method: 'template', validation_passed: true },
    ]),
    approve: vi.fn().mockResolvedValue({ status: 'ok' }),
    reject: vi.fn().mockResolvedValue({ status: 'ok' }),
  },
  streamingService: { list: vi.fn().mockResolvedValue({ pipelines: [{ name: 'p1', state: 'running' }], total: 1 }) },
  uploadService: { getUploadedFiles: vi.fn().mockResolvedValue([{ filename: 'a.csv', size: 1, modified: '' }]) },
}));

/* The classic-page registry is mocked so jsdom doesn't load every page chunk;
   integration of the real pages is covered by their own suites. */
vi.mock('./viewRegistry', () => ({
  VIEW_REGISTRY: { Dashboards: { component: () => null } },
  PAGE_ID_TO_NAV: {},
}));
vi.mock('./views', () => ({
  ViewHost: ({ nav }: { nav: string }) => <div data-testid="wb-view">mounted:{nav}</div>,
}));

import { healingService } from '../services/api';
import Workbench from './Workbench';

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const u = String(url);
    if (u.includes('/audit/ledger/verify')) return { ok: true, json: async () => ({ ok: true, count: 42, merkle_root: 'abcd'.repeat(16) }) } as Response;
    if (u.endsWith('/health')) return { ok: true, json: async () => ({ services: { gw: { status: 'healthy' }, cf: { status: 'healthy' } } }) } as Response;
    if (u.includes('/audit/financial/demo')) return { ok: true, json: async () => ({ record_hash: 'f'.repeat(64), n_findings: 9, materiality_threshold: 12345, signature_status: 'signed' }) } as Response;
    return { ok: false, json: async () => ({}) } as Response;
  }));
});

const boot = async () => {
  render(<Workbench />);
  fireEvent.click(screen.getByText('Continue with Okta'));
  await act(async () => { vi.advanceTimersByTime(420 * 7); });
};

describe('Workbench', () => {
  it('starts at the login screen with the design headline', () => {
    render(<Workbench />);
    expect(screen.getByTestId('wb-login')).toBeInTheDocument();
    expect(screen.getByText('Analysis your auditors can replay.')).toBeInTheDocument();
  });

  it('email sign-in calls the REAL auth service; failure shows the error', async () => {
    vi.mocked((await import('../services/api')).authService.login).mockRejectedValueOnce(new Error('Invalid credentials'));
    render(<Workbench />);
    fireEvent.change(screen.getByPlaceholderText('you@acme.com'), { target: { value: 'a@b.co' } });
    fireEvent.click(screen.getByText('Continue'));
    expect(await screen.findByText('Invalid credentials')).toBeInTheDocument();
  });

  it('cockpit renders every board panel from real (mocked) services', async () => {
    vi.useFakeTimers();
    await boot();
    for (const id of ['wb-stats', 'wb-chat', 'wb-cf', 'wb-healing', 'wb-pipes', 'wb-lineage', 'wb-history', 'wb-feed']) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
    vi.useRealTimers();
  });

  it('the cockpit shell is height-bounded so only the main column scrolls', async () => {
    vi.useFakeTimers();
    await boot();
    const app = screen.getByTestId('wb-app');
    expect(app.style.height).toBe('100vh');
    expect(app.style.overflow).toBe('hidden');
    vi.useRealTimers();
  });

  it('healing approve calls the REAL S41 service and records the signed override', async () => {
    vi.useFakeTimers();
    await boot();
    const approve = await screen.findByText('Approve & deploy');
    await act(async () => { fireEvent.click(approve); });
    expect(healingService.approve).toHaveBeenCalledWith('rec-1', 'workbench-ui');
    expect(screen.getByText('✓ approved — shim deploying, override signed')).toBeInTheDocument();
    vi.useRealTimers();
  });

  it('stats bar shows live ledger + health values, not seeds', async () => {
    vi.useFakeTimers();
    await boot();
    const stats = screen.getByTestId('wb-stats');
    expect(stats).toHaveTextContent('2/2');   // services from /health fixture
    expect(stats).toHaveTextContent('42');    // ledger records from verify fixture
    vi.useRealTimers();
  });

  it('a registered nav mounts the full classic module inside the shell', async () => {
    vi.useFakeTimers();
    await boot();
    fireEvent.click(screen.getByText('Dashboards'));
    expect(screen.getByTestId('wb-view')).toHaveTextContent('mounted:Dashboards');
    expect(screen.queryByTestId('wb-stub')).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it('an unregistered nav still gets the honest stub', async () => {
    vi.useFakeTimers();
    await boot();
    fireEvent.click(screen.getByText('Scheduler'));
    expect(screen.getByTestId('wb-stub')).toBeInTheDocument();
    vi.useRealTimers();
  });
});
