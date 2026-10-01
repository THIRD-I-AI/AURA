import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { financialAuditService, type FinancialAuditReport } from '../../../services/api';
import { useAuditDeck } from '../useAuditDeck';

const report = (hash: string) => ({ record_hash: hash, findings: [] }) as unknown as FinancialAuditReport;

// BUG-254: verify() read the record hash when it started but wrote its verdict
// whenever it finished -- so the verdict of the PREVIOUS audit could be shown
// against a report that was run while the verify call was in flight.
describe('BUG-254: useAuditDeck ignores a verify() answer for a superseded record', () => {
  afterEach(() => vi.restoreAllMocks());

  it('keeps the new report unverified when the old record\u2019s verify lands late', async () => {
    vi.spyOn(financialAuditService, 'ensureAuditorToken').mockResolvedValue(undefined as never);
    vi.spyOn(financialAuditService, 'runAudit')
      .mockResolvedValueOnce(report('old-hash'))
      .mockResolvedValueOnce(report('new-hash'));
    let resolveVerify: (v: never) => void = () => undefined;
    vi.spyOn(financialAuditService, 'verify').mockImplementation(
      () => new Promise((res) => { resolveVerify = res as (v: never) => void; }) as never,
    );

    const { result } = renderHook(() => useAuditDeck());
    await act(async () => { await result.current.runAudit(); });
    let pendingVerify: Promise<void> = Promise.resolve();
    act(() => { pendingVerify = result.current.verify(); });
    await act(async () => { await result.current.runAudit(); });
    expect(result.current.report?.record_hash).toBe('new-hash');

    await act(async () => {
      resolveVerify({ verified: true, chain_verified: true, signature_status: 'signed' } as never);
      await pendingVerify;
    });
    expect(result.current.verification).toBe('unverified');
  });
});
