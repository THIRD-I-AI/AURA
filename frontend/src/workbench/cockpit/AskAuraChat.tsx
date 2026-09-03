/* "Ask AURA" chat panel — commander SSE round-trip (generator → critic).
   Owns its own message/thinking state; reports back to the shared session
   feed and query-history table via the callbacks Workbench.tsx passes down. */
import { useRef, useState } from 'react';
import { chatService } from '../../services/api';
import type { Msg, HistoryEntry } from './types';

const now = () => new Date().toTimeString().slice(0, 5);

/* chatService.streamMessage throws plain Errors: 'commander_disabled' on 404,
   `stream failed: ${status}` on any other non-ok response, or whatever the
   fetch layer itself throws for a network/abort failure (no `status`, no
   parseable message) — branch the user-facing copy on which one it actually
   was instead of collapsing every cause into "offline". */
function describeChatError(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  const name = err instanceof Error ? err.name : undefined;
  if (name === 'AbortError') return 'Request cancelled.';
  const statusMatch = /^stream failed: (\d+)$/.exec(message);
  const status = statusMatch ? Number(statusMatch[1]) : null;
  if (status === 401 || status === 403) {
    return 'Your session has expired — please sign in again to continue.';
  }
  if (status !== null && status >= 500) {
    return 'Commander is temporarily unavailable (server error) — try again shortly.';
  }
  return 'Commander offline — showing the workflow with the connected gateway is required for live answers.';
}

type Props = {
  pushFeed: (k: string, color: string, t: string) => void;
  setHistory: React.Dispatch<React.SetStateAction<HistoryEntry[]>>;
};

export function AskAuraChat({ pushFeed, setHistory }: Props) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [thinking, setThinking] = useState<string | null>(null);
  const chatInput = useRef<HTMLInputElement>(null);

  const ask = async () => {
    const q = chatInput.current?.value.trim();
    if (!q || thinking) return;
    if (chatInput.current) chatInput.current.value = '';
    setMessages((m) => [...m, { q }]);
    setThinking('generator drafting SQL · critic reviewing…');
    let sql: string | undefined; let critic: string | undefined; let answer = '';
    try {
      await chatService.streamMessage(q, {
        onEvent: (ev: { event: string; data: Record<string, unknown> }) => {
          const d = ev.data as { name?: string; arguments?: { sql?: string }; result?: { row_count?: number }; text?: string; message?: string };
          if (ev.event === 'tool_call' && d.arguments?.sql) sql = d.arguments.sql;
          else if (ev.event === 'tool_result') critic = `verified · ${d.result?.row_count ?? 0} rows · sandboxed`;
          else if (ev.event === 'text') answer = d.text ?? '';
          else if (ev.event === 'error') answer = `Error: ${d.message}`;
        },
      });
    } catch (err) {
      answer = describeChatError(err);
    }
    setThinking(null);
    setMessages((m) => {
      const last = m[m.length - 1];
      return [...m.slice(0, -1), { ...last, sql, critic, answer: answer || '(no answer)' }];
    });
    pushFeed('QUERY', 'var(--text2)', `commander run: ${q.slice(0, 48)}`);
    setHistory((h) => [{ time: now(), q: q.length > 52 ? q.slice(0, 52) + '…' : q, engine: 'DuckDB', status: sql ? 'executed' : 'answered', cost: '—', dur: '—', by: 'you' }, ...h]);
  };

  return (
    <div className="aw-panel flex flex-col" data-testid="wb-chat">
      <div className="aw-panel-head" style={{ padding: '14px 18px' }}>
        <div className="text-[14px] font-semibold">Ask AURA</div>
        <div className="aw-chip aw-pill-outline">generator ⇄ critic</div>
        <div className="aw-chip aw-pill-accent">DPC cross-check</div>
        <div className="flex-1" />
        <div className="text-[11px] text-[var(--text3)]">DuckDB lake</div>
      </div>
      <div className="py-4 px-[18px] flex flex-col gap-3.5 max-h-[560px] overflow-y-auto">
        {messages.length === 0 && !thinking && (
          <div className="text-[12.5px] text-[var(--text3)] leading-[1.7] py-2.5 px-0">
            No conversation yet. Ask about your loaded datasets — the commander generates SQL,
            executes it in the sandbox, and streams the verified answer here.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className="flex flex-col gap-2.5 animate-[awup_.25s_ease]">
            <div className="self-end max-w-[70%] bg-[var(--raised)] border border-[var(--border)] rounded-[10px_10px_3px_10px] py-[9px] px-3.5 text-[13px]">{m.q}</div>
            {m.sql && <div className="aw-mono bg-[var(--sunken)] border border-[var(--hair)] rounded-none py-3 px-3.5 text-[11.5px] leading-[1.65] text-[var(--text2)] whitespace-pre-wrap">{m.sql}</div>}
            {m.critic && <div className="text-[11px] text-[var(--text3)]">{m.critic}</div>}
            {m.columns && m.rows && (
              <div className="border border-[var(--hair)] rounded-none overflow-hidden">
                <div className="flex bg-[var(--raised)]">{m.columns.map((c) => <div key={c} className="aw-mono flex-1 py-[7px] px-3.5 text-[10px] font-semibold text-[var(--text3)] tracking-[0.06em]">{c}</div>)}</div>
                {m.rows.map((r, ri) => <div key={ri} className="flex border-t border-[var(--hair)]">{r.map((cell, ci) => <div key={ci} className="aw-mono flex-1 py-[7px] px-3.5 text-[11.5px]">{cell}</div>)}</div>)}
              </div>
            )}
            {m.answer && <div className="text-[13px] leading-[1.55]">{m.answer}</div>}
          </div>
        ))}
        {thinking && <div className="aw-mono flex items-center gap-2.5 text-[11px] font-medium text-[var(--text3)]"><span className="aw-spinner" />{thinking}</div>}
      </div>
      <div className="pt-3 px-[18px] pb-4 border-t border-[var(--hair)] flex gap-2">
        <input ref={chatInput} onKeyDown={(e) => e.key === 'Enter' && ask()} placeholder="Ask anything about your data — SQL is generated, checked, and signed" className="aw-input flex-1 py-2.5 px-3.5 text-[13px]" />
        <button onClick={ask} className="aw-btn-accent text-[12.5px] py-2.5 px-[18px]">Ask</button>
      </div>
    </div>
  );
}
