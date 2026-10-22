import { afterEach, describe, expect, it, vi } from 'vitest';

// BUG-514: with VITE_API_URL unset (the same-origin production build),
// API_BASE_URL is the relative '/api/v1'. The inbound hook URL is copied into
// GitHub/Zapier/cron, which need an absolute URL with scheme and host.
describe('inboundHookService.fireUrl (BUG-514)', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('is absolute in the same-origin production build', async () => {
    vi.stubEnv('VITE_API_URL', '');
    vi.resetModules();
    const { inboundHookService, API_BASE_URL } = await import('../api');
    expect(API_BASE_URL).toBe('/api/v1');
    expect(inboundHookService.fireUrl('orders-arrived')).toBe(
      `${window.location.origin}/api/v1/hooks/fire/orders-arrived`,
    );
  });

  it('keeps an explicit cross-origin API base as is', async () => {
    vi.stubEnv('VITE_API_URL', 'https://api.example.com');
    vi.resetModules();
    const { inboundHookService } = await import('../api');
    expect(inboundHookService.fireUrl('a b')).toBe('https://api.example.com/api/v1/hooks/fire/a%20b');
  });
});
