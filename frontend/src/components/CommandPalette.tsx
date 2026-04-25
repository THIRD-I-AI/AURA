import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { type PageType } from './Layout/AppLayout';
import { savedQueryService, type SavedQuery } from '../services/api';

interface Command {
  id: string;
  label: string;
  hint?: string;
  group: 'navigate' | 'action' | 'saved';
  shortcut?: string;
  run: () => void;
}

interface CommandPaletteProps {
  onNavigate: (page: PageType) => void;
}

const PAGE_COMMANDS: Array<{ id: string; label: string; page: PageType; hint: string }> = [
  { id: 'go-dashboard',  label: 'Go to Dashboard',     page: 'dashboard',  hint: 'Overview & live metrics' },
  { id: 'go-chat',       label: 'Go to Chat',          page: 'chat',       hint: 'Ask questions about your data' },
  { id: 'go-files',      label: 'Go to Files & Data',  page: 'files',      hint: 'Manage uploads and connections' },
  { id: 'go-queries',    label: 'Go to Query History', page: 'queries',    hint: 'Replay previous SQL runs' },
  { id: 'go-library',    label: 'Go to Library',       page: 'library',    hint: 'Saved queries' },
  { id: 'go-dashboards', label: 'Go to Dashboards',    page: 'dashboards', hint: 'Composable saved-query tiles' },
  { id: 'go-lineage',    label: 'Go to Lineage',       page: 'lineage',    hint: 'Tables → queries → dashboards graph' },
  { id: 'go-cost',       label: 'Go to LLM Cost',      page: 'cost',       hint: 'Token usage by provider/model' },
  { id: 'go-agent',      label: 'Go to Agent',         page: 'agent',      hint: 'Agentic data engineering' },
  { id: 'go-pipelines',  label: 'Go to ETL Pipelines', page: 'pipelines',  hint: 'Build and run transformations' },
  { id: 'go-streaming',  label: 'Go to Streaming',     page: 'streaming',  hint: 'Real-time pipelines & metrics' },
  { id: 'go-webhooks',   label: 'Go to Webhooks',      page: 'webhooks',   hint: 'Inbound + outbound HTTP triggers' },
  { id: 'go-settings',   label: 'Open Settings',       page: 'settings',   hint: 'Preferences and configuration' },
];

const fuzzyScore = (haystack: string, needle: string): number => {
  if (!needle) return 1;
  const h = haystack.toLowerCase();
  const n = needle.toLowerCase();
  if (h === n) return 100;
  if (h.startsWith(n)) return 80;
  if (h.includes(n)) return 60;
  let last = -1;
  let runs = 0;
  for (const ch of n) {
    const idx = h.indexOf(ch, last + 1);
    if (idx === -1) return 0;
    if (idx === last + 1) runs += 1;
    last = idx;
  }
  return Math.max(1, 30 + runs * 2 - (h.length - n.length) / 4);
};

const isMac = typeof navigator !== 'undefined' && /Mac|iPad|iPhone/.test(navigator.platform);
const modKey = isMac ? '⌘' : 'Ctrl';

const CommandPalette: React.FC<CommandPaletteProps> = ({ onNavigate }) => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const [savedQueries, setSavedQueries] = useState<SavedQuery[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  // Global shortcut: Cmd/Ctrl+K toggles, ESC closes.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const cmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k';
      if (cmdK) {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === 'Escape' && open) {
        e.preventDefault();
        setOpen(false);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open]);

  // Lazy-load saved queries on first open; refresh each open so renames/stars show up.
  useEffect(() => {
    if (!open) return;
    savedQueryService.list().then(setSavedQueries).catch(() => setSavedQueries([]));
    setQuery('');
    setActiveIndex(0);
    // Defer focus until the input is mounted.
    setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  const close = useCallback(() => setOpen(false), []);

  const commands = useMemo<Command[]>(() => {
    const navCommands: Command[] = PAGE_COMMANDS.map((p) => ({
      id: p.id,
      label: p.label,
      hint: p.hint,
      group: 'navigate',
      run: () => { onNavigate(p.page); close(); },
    }));
    const savedCommands: Command[] = savedQueries.map((q) => ({
      id: `sq-${q.id}`,
      label: `Open: ${q.name}`,
      hint: q.prompt || q.sql.slice(0, 80),
      group: 'saved',
      run: () => {
        try {
          sessionStorage.setItem('aura.library.openQuery', JSON.stringify({
            id: q.id, name: q.name, sql: q.sql, prompt: q.prompt ?? null,
          }));
        } catch { /* sessionStorage may be blocked */ }
        onNavigate('chat');
        close();
      },
    }));
    return [...navCommands, ...savedCommands];
  }, [savedQueries, onNavigate, close]);

  const ranked = useMemo(() => {
    const scored = commands
      .map((c) => ({ c, score: fuzzyScore(c.label + ' ' + (c.hint ?? ''), query) }))
      .filter((x) => x.score > 0);
    scored.sort((a, b) => b.score - a.score);
    return scored.map((x) => x.c).slice(0, 30);
  }, [commands, query]);

  // Keep the active index in range as the result set shrinks.
  useEffect(() => {
    if (activeIndex >= ranked.length) setActiveIndex(0);
  }, [ranked.length, activeIndex]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((i) => Math.min(ranked.length - 1, i + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => Math.max(0, i - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const cmd = ranked[activeIndex];
      if (cmd) cmd.run();
    }
  };

  if (!open) return null;

  // Group dividers
  const groups: Array<{ label: string; items: Command[]; indexOffset: number }> = [];
  let offset = 0;
  for (const g of ['navigate', 'saved'] as const) {
    const items = ranked.filter((c) => c.group === g);
    if (items.length === 0) continue;
    groups.push({
      label: g === 'navigate' ? 'Pages' : 'Saved queries',
      items,
      indexOffset: offset,
    });
    offset += items.length;
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      onClick={(e) => { if (e.target === e.currentTarget) close(); }}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.55)',
        zIndex: 9999,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        paddingTop: '10vh',
        animation: 'fade-in 0.12s ease-out',
      }}
    >
      <div
        style={{
          width: 'min(640px, 92vw)',
          background: 'var(--bg-elevated, #1a1d24)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: '0 18px 60px rgba(0,0,0,0.55)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => { setQuery(e.target.value); setActiveIndex(0); }}
          onKeyDown={onKeyDown}
          placeholder="Type a command or search…"
          aria-label="Command palette search"
          style={{
            padding: 'var(--space-3) var(--space-4)',
            background: 'transparent',
            border: 'none',
            borderBottom: '1px solid var(--border-subtle)',
            color: 'var(--text-primary)',
            fontSize: 'var(--font-md)',
            fontFamily: 'var(--font-sans)',
            outline: 'none',
          }}
        />
        <div style={{ maxHeight: '50vh', overflowY: 'auto', padding: 'var(--space-2) 0' }}>
          {ranked.length === 0 ? (
            <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 'var(--font-sm)' }}>
              No matches
            </div>
          ) : (
            groups.map((g) => (
              <div key={g.label}>
                <div style={{ padding: '6px var(--space-4)', fontSize: 10, fontWeight: 600, color: 'var(--text-tertiary)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                  {g.label}
                </div>
                {g.items.map((c, i) => {
                  const idx = g.indexOffset + i;
                  const active = idx === activeIndex;
                  return (
                    <button
                      key={c.id}
                      role="option"
                      aria-selected={active}
                      onMouseEnter={() => setActiveIndex(idx)}
                      onClick={() => c.run()}
                      style={{
                        width: '100%',
                        padding: 'var(--space-2-5) var(--space-4)',
                        background: active ? 'var(--accent-dim, rgba(99,102,241,0.12))' : 'transparent',
                        border: 'none',
                        textAlign: 'left',
                        cursor: 'pointer',
                        color: 'var(--text-primary)',
                        fontFamily: 'var(--font-sans)',
                        display: 'flex',
                        alignItems: 'baseline',
                        gap: 'var(--space-3)',
                      }}
                    >
                      <span style={{ fontSize: 'var(--font-sm)', fontWeight: 500 }}>{c.label}</span>
                      {c.hint && (
                        <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                          {c.hint}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
        <div style={{ padding: 'var(--space-2) var(--space-4)', borderTop: '1px solid var(--border-subtle)', fontSize: 11, color: 'var(--text-tertiary)', display: 'flex', gap: 'var(--space-4)' }}>
          <span>↑↓ navigate</span>
          <span>↵ run</span>
          <span style={{ marginLeft: 'auto' }}>{modKey}+K toggle · Esc close</span>
        </div>
      </div>
    </div>
  );
};

export default CommandPalette;
