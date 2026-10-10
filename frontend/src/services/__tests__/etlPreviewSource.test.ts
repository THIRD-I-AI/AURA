import { afterEach, describe, expect, it, vi } from 'vitest';

import { etlService } from '../api';

function mockFetchOnce(status: number, body: unknown) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body,
  }) as unknown as typeof fetch;
}

// BUG-388: /etl/preview-source answers a file it cannot load with HTTP 200
// {status:'error', error}; previewSource returned that body as a preview, and the
// Pipelines view crashed reading row_count / columns from it.
describe('BUG-388: etlService.previewSource', () => {
  afterEach(() => vi.restoreAllMocks());

  it('rejects with the server error when the preview failed', async () => {
    mockFetchOnce(200, { status: 'error', error: 'could not read the file' });
    await expect(etlService.previewSource('bad.json')).rejects.toThrow('could not read the file');
  });

  it('returns a real preview unchanged', async () => {
    const preview = { table_name: 't', columns: [], row_count: 0, preview: [] };
    mockFetchOnce(200, preview);
    await expect(etlService.previewSource('ok.csv')).resolves.toEqual(preview);
  });
});
