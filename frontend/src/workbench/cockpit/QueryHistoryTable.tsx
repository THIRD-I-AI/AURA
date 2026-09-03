/* Query history board card — this session's recent runs. */
import { cn } from '../../lib/cn';
import type { HistoryEntry } from './types';

export function QueryHistoryTable({ history }: { history: HistoryEntry[] }) {
  return (
    <div className="aw-panel" data-testid="wb-history">
      <div className="aw-panel-head"><div className="aw-panel-title">Query history</div><div className="flex-1" /><div className="text-[11px] text-[var(--text3)]">this session + today</div></div>
      <div className="text-[11.5px]">
        <div className="aw-table-head grid grid-cols-[.55fr_2.6fr_.8fr_.7fr_.55fr_.6fr_.7fr]">{['TIME', 'QUERY', 'ENGINE', 'STATUS', 'COST', 'DUR', 'BY'].map((h) => <div key={h} className="py-[7px] px-4">{h}</div>)}</div>
        {history.length === 0 && <div className="py-3 px-4 text-xs text-[var(--text3)]">No queries recorded yet in this workspace.</div>}
        {history.map((hq, i) => (
          <div key={i} className="grid grid-cols-[.55fr_2.6fr_.8fr_.7fr_.55fr_.6fr_.7fr] border-t border-[var(--hair)] items-center">
            <div className="aw-mono py-2 px-4 text-[11px] text-[var(--text3)]">{hq.time}</div>
            <div className="py-2 px-4">{hq.q}</div>
            <div className="aw-mono py-2 px-4 text-[11px]">{hq.engine}</div>
            <div className={cn('py-2 px-4 font-semibold', hq.status === 'signed' ? 'text-[var(--accent)]' : 'text-[var(--text2)]')}>{hq.status}</div>
            <div className="aw-mono py-2 px-4 text-[11px]">{hq.cost}</div>
            <div className="aw-mono py-2 px-4 text-[11px]">{hq.dur}</div>
            <div className="py-2 px-4 text-[var(--text3)]">{hq.by}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
