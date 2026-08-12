/* Ask AURA — native chat panel. shadcn/ui + Tailwind (frontend/CLAUDE.md):
   ui-kit primitives (Panel/PanelBody/Button/EmptyState) + token utilities, no
   inline styles. Core ask -> schema-driven SQL -> executed answer, via
   chatService.sendMessage (POST /chat). */
import { useCallback, useRef, useState } from 'react';
import { SendHorizonal } from 'lucide-react';

import { Panel, PanelBody } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { cn } from '@/lib/cn';
import { chatService } from '../../services/api';

type Msg = {
  id: string;
  role: 'user' | 'assistant';
  text?: string;
  sql?: string;
  columns?: string[];
  data?: Record<string, unknown>[];
  rowCount?: number;
  error?: string;
  pending?: boolean;
};

let _seq = 0;
const nextId = () => `m${_seq++}`;

export default function AskAuraPanel() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const send = useCallback(async () => {
    const q = input.trim();
    if (!q || busy) return;
    setInput('');
    setBusy(true);
    const pending: Msg = { id: nextId(), role: 'assistant', pending: true };
    setMessages((m) => [...m, { id: nextId(), role: 'user', text: q }, pending]);
    try {
      const resp = await chatService.sendMessage(q);
      const er = (resp as { execution_result?: Record<string, unknown> }).execution_result || {};
      const sql = (resp as { final_query?: string }).final_query;
      const ok = (resp as { status?: string }).status !== 'Error' && er.success !== false;
      const filled: Msg = {
        id: pending.id,
        role: 'assistant',
        text: (er.conclusion as string) || (er.sql_explanation as string) || (ok ? 'Done.' : undefined),
        sql: sql || undefined,
        columns: (er.columns as string[]) || undefined,
        data: (er.data as Record<string, unknown>[]) || undefined,
        rowCount: typeof er.row_count === 'number' ? (er.row_count as number) : undefined,
        error: ok ? undefined : ((er.error as string) || 'Query failed.'),
      };
      setMessages((m) => m.map((x) => (x.id === pending.id ? filled : x)));
    } catch {
      setMessages((m) => m.map((x) => (x.id === pending.id ? { ...x, pending: false, error: 'Could not reach the gateway.' } : x)));
    } finally {
      setBusy(false);
      requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }));
    }
  }, [input, busy]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3" data-testid="wb-ask-panel">
      <p className="font-mono text-2xs text-text-tertiary">
        generator ⇄ critic · DPC cross-check · SQL generated from your data&apos;s schema, executed in the sandbox, signed
      </p>

      <Panel className="flex-1">
        <PanelBody ref={scrollRef} className="flex flex-col gap-3">
          {messages.length === 0 ? (
            <EmptyState
              intent="awaiting"
              title="Ask your data"
              description="AURA reads the schema, generates SQL, runs it, and shows the answer with its query."
            />
          ) : (
            messages.map((m) =>
              m.role === 'user' ? (
                <div key={m.id} className="self-end max-w-[80%] border border-border bg-secondary px-3 py-2 text-sm text-card-foreground">
                  {m.text}
                </div>
              ) : (
                <div key={m.id} className="flex max-w-[92%] flex-col gap-1.5 self-start">
                  {m.pending && <div className="font-mono text-xs text-text-tertiary">◌ generating SQL · executing…</div>}
                  {m.error && (
                    <div className="border border-border bg-secondary px-2.5 py-1.5 font-mono text-xs text-destructive">{m.error}</div>
                  )}
                  {m.text && <div className="text-sm leading-relaxed text-card-foreground">{m.text}</div>}
                  {m.sql && (
                    <pre className="overflow-x-auto whitespace-pre-wrap border border-border bg-secondary px-2.5 py-2 font-mono text-xs text-signal">{m.sql}</pre>
                  )}
                  {m.data && m.columns && m.data.length > 0 && (
                    <div className="overflow-x-auto border border-border">
                      <table className="w-full border-collapse font-mono text-xs">
                        <thead>
                          <tr>
                            {m.columns.map((c) => (
                              <th key={c} className="whitespace-nowrap border-b border-border px-2.5 py-1.5 text-left font-semibold text-text-tertiary">{c}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {m.data.slice(0, 12).map((row, i) => (
                            <tr key={i}>
                              {m.columns!.map((c) => (
                                <td key={c} className="whitespace-nowrap border-t border-border px-2.5 py-1.5 text-text-secondary">{String(row[c] ?? '')}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {typeof m.rowCount === 'number' && (
                    <div className="font-mono text-2xs text-text-tertiary">
                      {m.rowCount} row{m.rowCount === 1 ? '' : 's'}{m.data && m.data.length > 12 ? ' · showing first 12' : ''}
                    </div>
                  )}
                </div>
              ),
            )
          )}
        </PanelBody>
      </Panel>

      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') send(); }}
          placeholder="Ask anything about your data — SQL is generated, checked, and signed"
          aria-label="Ask AURA"
          className={cn(
            'flex-1 rounded-none border border-border bg-secondary px-3.5 py-2 text-sm text-card-foreground',
            'placeholder:text-text-tertiary outline-none focus-visible:border-ring',
          )}
        />
        <Button size="sm" onClick={send} disabled={busy || !input.trim()} className="px-4">
          {busy ? '…' : <>Ask <SendHorizonal /></>}
        </Button>
      </div>
    </div>
  );
}
