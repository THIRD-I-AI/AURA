import { describe, expect, it } from 'vitest';

import type { QueryResponse } from '../api';
import { chatOutcome } from '../chatResponse';

const r = (over: Partial<QueryResponse>): QueryResponse => ({ job_id: 'j', status: 'Success', ...over });

describe('chatOutcome', () => {
  it('reads a query answer from the analysis first', () => {
    expect(chatOutcome(r({ execution_result: { success: true, conclusion: 'EU leads', sql_explanation: 'sums' } })))
      .toEqual({ ok: true, answer: 'EU leads' });
  });

  it('reads a reply with no query from message (BUG-424)', () => {
    expect(chatOutcome(r({ status: 'Conversational', message: 'Hello!' }))).toEqual({ ok: true, answer: 'Hello!' });
    expect(chatOutcome(r({ status: 'PipelineCreated', message: 'Pipeline saved' })).answer).toBe('Pipeline saved');
  });

  it('treats an HTTP 200 with status Error as a failure, most specific reason first (BUG-425)', () => {
    expect(chatOutcome(r({ status: 'Error', error_message: 'rate-limited', message: 'm' })))
      .toEqual({ ok: false, error: 'rate-limited' });
    expect(chatOutcome(r({ execution_result: { success: false, error: 'no such column' }, error_message: 'x' })))
      .toEqual({ ok: false, error: 'no such column' });
    expect(chatOutcome(r({ status: 'Error', message: 'no source file named' })).error).toBe('no source file named');
    expect(chatOutcome(r({ status: 'Error' })).error).toBe('Query failed.');
  });
});
