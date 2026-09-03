/* Shared shape types for the Cockpit board's extracted panels. */

export type Msg = { q: string; sql?: string; critic?: string; columns?: string[]; rows?: string[][]; answer?: string };
export type Heal = { id: string; title: string; method: string; safe: boolean; sub: string; state: 'pending' | 'deployed' | 'rejected'; resolution?: string };
export type FeedEv = { time: string; k: string; color: string; t: string };
export type HistoryEntry = { time: string; q: string; engine: string; status: string; cost: string; dur: string; by: string };

export type CfState =
  | { status: 'idle' }
  | { status: 'running'; stageIdx: number }
  | { status: 'done'; nFindings: number | null; materiality: string | null; hash: string | null; verifyUrl: string | null; raw: string }
  | { status: 'error'; message: string };
