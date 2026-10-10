import { afterEach, describe, expect, it, vi } from 'vitest';

import { connectorService } from '../api';

function mockFetchOnce(status: number, body: unknown) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body,
  }) as unknown as typeof fetch;
}

// BUG-391: GET /connections/{id}/schema reports a failure as HTTP 200
// {success:false, error, schema:{}}; getSchema returned the empty schema, and the
// Connectors picker said "No tables found on this connection" instead of the error.
describe('BUG-391: connectorService.getSchema', () => {
  afterEach(() => vi.restoreAllMocks());

  it('rejects with the server error when the schema read failed', async () => {
    mockFetchOnce(200, { success: false, error: 'connection refused', schema: {} });
    await expect(connectorService.getSchema('c1')).rejects.toThrow('connection refused');
  });

  it('returns the schema on success', async () => {
    mockFetchOnce(200, { success: true, schema: { orders: ['id'] } });
    await expect(connectorService.getSchema('c1')).resolves.toEqual({ orders: ['id'] });
  });
});
