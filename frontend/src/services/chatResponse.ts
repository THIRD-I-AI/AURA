import type { QueryResponse } from './api';

/** What a POST /chat answer means for the person who asked.
 *
 *  The one place the frontend decides this. Each chat surface used to work it out on
 *  its own and they disagreed: one read only `execution_result` and showed "Done." for
 *  a conversational reply carried in `message` (BUG-424), another never looked at
 *  `status` and showed nothing at all for a failure reported as HTTP 200 (BUG-425).
 *
 *  `/chat` reports a failure as HTTP 200 with `status: 'Error'` or
 *  `execution_result.success === false`; the reason can be in
 *  `execution_result.error`, `error_message` or `message`, most specific first.
 *  A success's text can be the analysis, the SQL's explanation, or -- for replies
 *  with no query at all (conversation, pipeline created, audit completed) -- `message`. */
export interface ChatOutcome {
  ok: boolean;
  answer?: string;
  error?: string;
}

export function chatOutcome(resp: QueryResponse): ChatOutcome {
  const er = resp.execution_result;
  const ok = resp.status !== 'Error' && er?.success !== false;
  if (ok) {
    return { ok, answer: er?.conclusion || er?.sql_explanation || resp.message || 'Done.' };
  }
  return { ok, error: er?.error || resp.error_message || resp.message || 'Query failed.' };
}
