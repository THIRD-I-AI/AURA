import { afterEach, describe, expect, it, vi } from 'vitest';

import { getAuthToken, setAuthToken, subscribeAuthChanged } from '../api';

// BUG-392: a sign-out (or a different sign-in) in another tab changed localStorage,
// but this tab kept its in-memory token and went on acting as the previous user.
describe('BUG-392: the token follows other tabs', () => {
  afterEach(() => { setAuthToken(null); vi.restoreAllMocks(); });

  function otherTabWrites(value: string | null) {
    if (value === null) localStorage.removeItem('aura.authToken');
    else localStorage.setItem('aura.authToken', value);
    window.dispatchEvent(new StorageEvent('storage', { key: 'aura.authToken', newValue: value }));
  }

  it('drops the token when another tab signs out, and notifies', () => {
    setAuthToken('tab-token');
    const changed = vi.fn();
    const unsubscribe = subscribeAuthChanged(changed);

    otherTabWrites(null);

    expect(getAuthToken()).toBeNull();
    expect(changed).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it('picks up a different account signed in elsewhere', () => {
    setAuthToken('alice');
    otherTabWrites('bob');
    expect(getAuthToken()).toBe('bob');
  });
});
