/* Session events feed — live log of real actions only (queries, audits,
   approvals). Workbench.tsx pushes into `feed` via pushFeed. */
import type { FeedEv } from './types';

export function SessionEventsFeed({ feed }: { feed: FeedEv[] }) {
  return (
    <div className="aw-panel" data-testid="wb-feed" role="log" aria-live="polite" aria-label="Session events">
      <div className="flex items-center gap-2 py-3 px-4"><span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] animate-[awpulse_1.6s_infinite]" /><div className="aw-panel-title">Session events</div><div className="flex-1" /><div className="text-[10.5px] text-[var(--text3)]">real actions only — queries · audits · approvals</div></div>
      {feed.length === 0 && <div className="py-2.5 px-4 border-t border-[var(--hair)] text-[11.5px] text-[var(--text3)]">No events yet — run a query or an audit and it lands here.</div>}
      {feed.map((ev, i) => (
        <div key={i} className="aw-mono flex gap-2.5 items-baseline py-1.5 px-4 border-t border-[var(--hair)] text-[10.5px]">
          <span className="text-[var(--text3)] flex-none">{ev.time}</span>
          <span className="flex-none font-bold text-[9px] tracking-[.06em]" style={{ color: ev.color }}>{ev.k}</span>
          <span className="text-[var(--text2)]">{ev.t}</span>
        </div>
      ))}
    </div>
  );
}
