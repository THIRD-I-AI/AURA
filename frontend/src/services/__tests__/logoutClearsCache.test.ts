import { beforeEach, describe, expect, it } from 'vitest';

import { authService, setAuthToken } from '../api';

function fakeToken(claims: Record<string, unknown>): string {
  return `h.${btoa(JSON.stringify(claims))}.s`;
}

// BUG-108: authService.logout() only cleared the auth token -- it never
// touched recentUploads/queryHistory, both of which store/index.tsx still
// reads as a fallback. On a shared device, stale data left by an older
// build could hydrate under the next user's session after logout.
describe('BUG-108: logout clears legacy localStorage cache keys', () => {
  beforeEach(() => {
    setAuthToken(fakeToken({ sub: 'u1' }));
    localStorage.setItem('recentUploads', JSON.stringify([{ id: 'f1', name: 'secret.csv' }]));
    localStorage.setItem('queryHistory', JSON.stringify([{ q: 'select * from secret' }]));
  });

  it('removes recentUploads and queryHistory on logout', () => {
    authService.logout();

    expect(localStorage.getItem('recentUploads')).toBeNull();
    expect(localStorage.getItem('queryHistory')).toBeNull();
  });

  it('also clears the auth token as before', () => {
    authService.logout();

    expect(authService.currentUser()).toBeNull();
  });
});
