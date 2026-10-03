import { afterEach, describe, expect, it, vi } from 'vitest';

import apiClient, { ApiRequestError, errorBodyMessage } from '../api';

/** ApiClient read `message` (the gateway's own error shape) but FastAPI's
 *  HTTPException sends `detail`, and it threw a plain object -- so callers
 *  written as `e instanceof Error ? e.message : String(e)` showed
 *  "[object Object]" or a bare status text instead of the server's reason. */
function respond(status: number, body: unknown) {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), {
    status, statusText: 'Forbidden', headers: { 'Content-Type': 'application/json' },
  }))));
}

describe('ApiClient errors', () => {
  afterEach(() => vi.unstubAllGlobals());

  it("throws a real Error carrying FastAPI's detail", async () => {
    respond(403, { detail: 'auditor or admin role required' });

    const err = await apiClient.post('/audit/decide', {}).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(ApiRequestError);
    expect((err as Error).message).toBe('auditor or admin role required');
    expect(String(err)).not.toContain('[object Object]');
    expect((err as ApiRequestError).status).toBe(403);
  });

  it("still prefers the gateway's own message shape", async () => {
    respond(409, { error: 'CONFLICT', message: 'name already in use' });

    const err = await apiClient.get('/x').catch((e: unknown) => e) as Error;

    expect(err.message).toBe('name already in use');
  });

  it('turns a 422 validation list into readable text', () => {
    expect(errorBodyMessage({ detail: [{ loc: ['body', 'url'], msg: 'field required' }, { msg: 'invalid url' }] }))
      .toBe('field required; invalid url');
    expect(errorBodyMessage({})).toBeNull();
    expect(errorBodyMessage(null)).toBeNull();
  });
});
