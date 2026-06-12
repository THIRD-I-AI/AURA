import { describe, expect, it } from 'vitest';

import { healthHint } from '../useSystemHealth';

describe('healthHint', () => {
  // S35b: the dashboard claimed "All services operational" off a
  // gateway-only /health probe while the services card showed 1/8.
  // The hint must only claim what the probe actually measured.
  it('describes the gateway probe, never all services', () => {
    expect(healthHint('healthy')).toBe('Gateway healthy');
    expect(healthHint('degraded')).toBe('Some services degraded');
    expect(healthHint('down')).toBe('Gateway unreachable');
  });
});
