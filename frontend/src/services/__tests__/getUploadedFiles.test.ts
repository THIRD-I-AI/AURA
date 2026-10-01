import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { setAuthToken, uploadService } from '../api';

function fakeToken(claims: Record<string, unknown>): string {
  return `h.${btoa(JSON.stringify(claims))}.s`;
}

function mockFetchOnce(status: number, body: unknown = {}) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body,
  }) as unknown as typeof fetch;
}

// BUG-247: getUploadedFiles caught every error and returned [], so a gateway
// outage or a forbidden request rendered as "No datasets" and every caller's
// error branch (DatasetsPanel, FilesAndDataPanel, the store's offline
// fallback) was dead code.
describe('BUG-247: getUploadedFiles propagates failures instead of returning []', () => {
  beforeEach(() => setAuthToken(fakeToken({ sub: 'u1' })));
  afterEach(() => vi.restoreAllMocks());

  it('rejects on a 500', async () => {
    mockFetchOnce(500, { message: 'boom' });
    await expect(uploadService.getUploadedFiles()).rejects.toMatchObject({ status: 500 });
  });

  it('rejects on a 403', async () => {
    mockFetchOnce(403, { message: 'forbidden' });
    await expect(uploadService.getUploadedFiles()).rejects.toMatchObject({ status: 403 });
  });

  it('resolves to the file list on success, and [] when the server sends none', async () => {
    const files = [{ filename: 'a.csv', size: 1, modified: '2026-01-01T00:00:00Z' }];
    mockFetchOnce(200, { status: 'ok', files });
    await expect(uploadService.getUploadedFiles()).resolves.toEqual(files);
    mockFetchOnce(200, { status: 'ok' });
    await expect(uploadService.getUploadedFiles()).resolves.toEqual([]);
  });
});
