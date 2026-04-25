import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { type PageType } from '../components/Layout/AppLayout';
import {
  savedQueryService,
  subscribeWorkspace,
  type SavedQuery,
  type SavedQuerySchedule,
  type SavedQueryRun,
} from '../services/api';

interface LibraryProps {
  setCurrentPage?: (page: PageType) => void;
}

const StarIcon: React.FC<{ filled?: boolean }> = ({ filled }) => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill={filled ? '#fbbf24' : 'none'} stroke={filled ? '#fbbf24' : 'currentColor'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
  </svg>
);

const Library: React.FC<LibraryProps> = ({ setCurrentPage }) => {
  const [queries, setQueries] = useState<SavedQuery[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState<string>('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await savedQueryService.list();
      setQueries(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load saved queries');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Re-fetch when the user switches workspace so the list reflects
  // the new tenancy without a full reload.
  useEffect(() => subscribeWorkspace(() => { refresh(); }), [refresh]);

  const filtered = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return queries;
    return queries.filter((q) =>
      q.name.toLowerCase().includes(f)
      || q.sql.toLowerCase().includes(f)
      || (q.prompt ?? '').toLowerCase().includes(f),
    );
  }, [queries, filter]);

  const toggleStar = async (q: SavedQuery) => {
    const next = !q.starred;
    setQueries((prev) => prev.map((x) => (x.id === q.id ? { ...x, starred: next } : x)));
    try {
      await savedQueryService.update(q.id, { starred: next });
    } catch (err) {
      setQueries((prev) => prev.map((x) => (x.id === q.id ? { ...x, starred: q.starred } : x)));
      setError(err instanceof Error ? err.message : 'Failed to toggle star');
    }
  };

  const beginRename = (q: SavedQuery) => {
    setEditingId(q.id);
    setEditingName(q.name);
  };

  const commitRename = async () => {
    if (!editingId) return;
    const target = queries.find((q) => q.id === editingId);
    if (!target) { setEditingId(null); return; }
    const newName = editingName.trim();
    setEditingId(null);
    if (!newName || newName === target.name) return;
    setQueries((prev) => prev.map((x) => (x.id === target.id ? { ...x, name: newName } : x)));
    try {
      await savedQueryService.update(target.id, { name: newName });
    } catch (err) {
      setQueries((prev) => prev.map((x) => (x.id === target.id ? { ...x, name: target.name } : x)));
      setError(err instanceof Error ? err.message : 'Failed to rename');
    }
  };

  const onDelete = async (q: SavedQuery) => {
    if (!window.confirm(`Delete saved query "${q.name}"?`)) return;
    const snapshot = queries;
    setQueries((prev) => prev.filter((x) => x.id !== q.id));
    try {
      await savedQueryService.remove(q.id);
    } catch (err) {
      setQueries(snapshot);
      setError(err instanceof Error ? err.message : 'Failed to delete');
    }
  };

  // ── Schedule modal state ─────────────────────────────────────────
  const [scheduleTarget, setScheduleTarget] = useState<SavedQuery | null>(null);
  const [scheduleDraft, setScheduleDraft] = useState<SavedQuerySchedule>({
    interval: 'daily', hour: 9, minute: 0, day_of_week: 0, enabled: true,
  });
  const [savingSchedule, setSavingSchedule] = useState(false);

  const openScheduleModal = (q: SavedQuery) => {
    setScheduleTarget(q);
    setScheduleDraft(q.schedule ?? {
      interval: 'daily', hour: 9, minute: 0, day_of_week: 0, enabled: true,
    });
  };

  const saveSchedule = async () => {
    if (!scheduleTarget) return;
    setSavingSchedule(true);
    try {
      const updated = await savedQueryService.setSchedule(scheduleTarget.id, scheduleDraft);
      setQueries((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));
      setScheduleTarget(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save schedule');
    } finally {
      setSavingSchedule(false);
    }
  };

  const removeSchedule = async (q: SavedQuery) => {
    if (!window.confirm(`Remove schedule on "${q.name}"?`)) return;
    try {
      const updated = await savedQueryService.clearSchedule(q.id);
      setQueries((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to clear schedule');
    }
  };

  // ── Runs panel state ─────────────────────────────────────────────
  const [runsOpenId, setRunsOpenId] = useState<string | null>(null);
  const [runsById, setRunsById] = useState<Record<string, SavedQueryRun[]>>({});
  const [runsLoadingId, setRunsLoadingId] = useState<string | null>(null);

  const toggleRuns = async (q: SavedQuery) => {
    if (runsOpenId === q.id) { setRunsOpenId(null); return; }
    setRunsOpenId(q.id);
    if (runsById[q.id]) return;
    setRunsLoadingId(q.id);
    try {
      const runs = await savedQueryService.listRuns(q.id, 20);
      setRunsById((prev) => ({ ...prev, [q.id]: runs }));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load runs');
    } finally {
      setRunsLoadingId(null);
    }
  };

  // ── Share modal state ────────────────────────────────────────────
  const [shareTarget, setShareTarget] = useState<SavedQuery | null>(null);
  const [shareToken, setShareToken] = useState<string | null>(null);
  const [shareLoading, setShareLoading] = useState(false);

  const openShareModal = async (q: SavedQuery) => {
    setShareTarget(q);
    setShareToken(null);
    setShareLoading(true);
    try {
      const { token } = await savedQueryService.share(q.id);
      setShareToken(token);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create share link');
      setShareTarget(null);
    } finally {
      setShareLoading(false);
    }
  };

  const revokeShare = async () => {
    if (!shareTarget) return;
    try {
      await savedQueryService.unshare(shareTarget.id);
      setShareToken(null);
      setShareTarget(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke share link');
    }
  };

  const openInChat = (q: SavedQuery) => {
    try {
      sessionStorage.setItem('aura.library.openQuery', JSON.stringify({
        id: q.id, name: q.name, sql: q.sql, prompt: q.prompt ?? null,
      }));
    } catch { /* sessionStorage may be blocked */ }
    setCurrentPage?.('chat');
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {/* Toolbar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by name, SQL, or prompt…"
          aria-label="Filter saved queries"
          style={{
            flex: '1 1 280px',
            minWidth: 0,
            padding: 'var(--space-2-5) var(--space-3)',
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-primary)',
            fontSize: 'var(--font-sm)',
            fontFamily: 'var(--font-sans)',
          }}
        />
        <button
          onClick={refresh}
          disabled={loading}
          style={{
            padding: 'var(--space-2) var(--space-4)',
            background: 'var(--bg-surface-2)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-secondary)',
            fontSize: 'var(--font-sm)',
            fontFamily: 'var(--font-sans)',
            cursor: loading ? 'not-allowed' : 'pointer',
            opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
        <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>
          {queries.length} saved · {queries.filter((q) => q.starred).length} starred
        </span>
      </div>

      {error && (
        <div role="alert" style={{ padding: 'var(--space-3)', background: 'var(--red-dim, rgba(239,68,68,0.08))', border: '1px solid var(--red, #f87171)', borderRadius: 'var(--radius-md)', color: 'var(--red, #f87171)', fontSize: 'var(--font-sm)' }}>
          {error}
        </div>
      )}

      {/* List */}
      {!loading && filtered.length === 0 ? (
        <div style={{ padding: 'var(--space-8)', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 'var(--font-sm)', border: '1px dashed var(--border-default)', borderRadius: 'var(--radius-md)' }}>
          {queries.length === 0
            ? 'No saved queries yet. Use "Save query" on a chat result to add one.'
            : 'No saved queries match this filter.'}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
          {filtered.map((q) => (
            <div
              key={q.id}
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 'var(--space-2)',
                padding: 'var(--space-3) var(--space-4)',
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                <button
                  onClick={() => toggleStar(q)}
                  aria-label={q.starred ? 'Unstar query' : 'Star query'}
                  aria-pressed={q.starred}
                  style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--text-tertiary)' }}
                >
                  <StarIcon filled={q.starred} />
                </button>
                {editingId === q.id ? (
                  <input
                    autoFocus
                    value={editingName}
                    onChange={(e) => setEditingName(e.target.value)}
                    onBlur={commitRename}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commitRename();
                      else if (e.key === 'Escape') { setEditingId(null); setEditingName(''); }
                    }}
                    style={{
                      flex: 1,
                      padding: '4px 8px',
                      background: 'var(--bg-sunken)',
                      border: '1px solid var(--accent)',
                      borderRadius: 'var(--radius-sm)',
                      color: 'var(--text-primary)',
                      fontSize: 'var(--font-sm)',
                      fontFamily: 'var(--font-sans)',
                    }}
                  />
                ) : (
                  <button
                    onClick={() => beginRename(q)}
                    title="Click to rename"
                    style={{ flex: 1, textAlign: 'left', background: 'transparent', border: 'none', cursor: 'text', padding: 0, color: 'var(--text-primary)', fontSize: 'var(--font-sm)', fontWeight: 600, fontFamily: 'var(--font-sans)' }}
                  >
                    {q.name}
                  </button>
                )}
                <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                  {new Date(q.updated_at).toLocaleString()}
                </span>
              </div>

              {q.prompt && (
                <div style={{ fontSize: 'var(--font-xs)', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                  <span style={{ color: 'var(--text-tertiary)', marginRight: 4 }}>Prompt:</span>
                  {q.prompt}
                </div>
              )}

              <pre style={{ margin: 0, padding: 'var(--space-2-5) var(--space-3)', background: 'var(--bg-sunken)', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-sm)', fontFamily: 'var(--font-mono)', fontSize: 12, color: '#a5b4fc', lineHeight: 1.6, overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {q.sql}
              </pre>

              {q.schedule?.enabled && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', fontSize: 12, color: 'var(--text-secondary)' }}>
                  <span aria-hidden style={{ color: '#fbbf24' }}>⏱</span>
                  <span>
                    {formatSchedule(q.schedule)} · next run {q.next_run_at ? new Date(q.next_run_at).toLocaleString() : '—'}
                  </span>
                  {q.last_run_at && (
                    <span style={{ color: 'var(--text-tertiary)' }}>
                      · last {new Date(q.last_run_at).toLocaleString()}
                    </span>
                  )}
                </div>
              )}

              <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
                <button
                  onClick={() => openInChat(q)}
                  style={{ padding: '5px 12px', fontSize: 12, fontWeight: 500, background: 'var(--accent)', color: 'white', border: 'none', borderRadius: 'var(--radius-sm)', cursor: 'pointer', fontFamily: 'var(--font-sans)' }}
                >
                  Open in chat
                </button>
                <button
                  onClick={() => navigator.clipboard?.writeText(q.sql)}
                  style={{ padding: '5px 12px', fontSize: 12, background: 'var(--bg-surface-2)', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', cursor: 'pointer', fontFamily: 'var(--font-sans)' }}
                >
                  Copy SQL
                </button>
                <button
                  onClick={() => openScheduleModal(q)}
                  style={{ padding: '5px 12px', fontSize: 12, background: 'var(--bg-surface-2)', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', cursor: 'pointer', fontFamily: 'var(--font-sans)' }}
                >
                  {q.schedule?.enabled ? 'Edit schedule' : 'Schedule'}
                </button>
                {q.schedule && (
                  <button
                    onClick={() => removeSchedule(q)}
                    style={{ padding: '5px 12px', fontSize: 12, background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', cursor: 'pointer', fontFamily: 'var(--font-sans)' }}
                  >
                    Unschedule
                  </button>
                )}
                <button
                  onClick={() => toggleRuns(q)}
                  style={{ padding: '5px 12px', fontSize: 12, background: 'var(--bg-surface-2)', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', cursor: 'pointer', fontFamily: 'var(--font-sans)' }}
                >
                  {runsOpenId === q.id ? 'Hide runs' : 'Runs'}
                </button>
                <button
                  onClick={() => openShareModal(q)}
                  style={{ padding: '5px 12px', fontSize: 12, background: 'var(--bg-surface-2)', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', cursor: 'pointer', fontFamily: 'var(--font-sans)' }}
                >
                  Share
                </button>
                <button
                  onClick={() => onDelete(q)}
                  style={{ padding: '5px 12px', fontSize: 12, background: 'transparent', color: 'var(--red, #f87171)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', cursor: 'pointer', fontFamily: 'var(--font-sans)', marginLeft: 'auto' }}
                >
                  Delete
                </button>
              </div>

              {runsOpenId === q.id && (
                <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: 'var(--space-2)' }}>
                  {runsLoadingId === q.id ? (
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Loading runs…</div>
                  ) : (runsById[q.id] ?? []).length === 0 ? (
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                      No runs yet. Scheduled runs fire automatically; wait for the next tick.
                    </div>
                  ) : (
                    <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {(runsById[q.id] ?? []).map((r) => (
                        <li key={r.id} style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                          <span style={{ minWidth: 150 }}>{new Date(r.started_at).toLocaleString()}</span>
                          <span style={{
                            padding: '1px 6px',
                            borderRadius: 4,
                            background: r.status === 'success' ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)',
                            color: r.status === 'success' ? '#34d399' : '#f87171',
                            fontWeight: 600,
                          }}>{r.status}</span>
                          <span>{r.row_count} rows</span>
                          <span style={{ color: 'var(--text-tertiary)' }}>{r.execution_time_ms} ms</span>
                          {r.error && <span style={{ color: '#f87171' }}>· {r.error}</span>}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {scheduleTarget && (
        <ScheduleModal
          query={scheduleTarget}
          draft={scheduleDraft}
          onDraftChange={setScheduleDraft}
          onCancel={() => setScheduleTarget(null)}
          onSave={saveSchedule}
          saving={savingSchedule}
        />
      )}

      {shareTarget && (
        <ShareModal
          query={shareTarget}
          token={shareToken}
          loading={shareLoading}
          onClose={() => { setShareTarget(null); setShareToken(null); }}
          onRevoke={revokeShare}
        />
      )}
    </div>
  );
};

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function formatSchedule(s: SavedQuerySchedule): string {
  const time = `${String(s.hour).padStart(2, '0')}:${String(s.minute).padStart(2, '0')}`;
  if (s.interval === 'hourly') return `Hourly at :${String(s.minute).padStart(2, '0')}`;
  if (s.interval === 'daily') return `Daily at ${time} UTC`;
  const day = DAY_LABELS[Math.max(0, Math.min(6, s.day_of_week ?? 0))];
  return `Weekly on ${day} at ${time} UTC`;
}

interface ScheduleModalProps {
  query: SavedQuery;
  draft: SavedQuerySchedule;
  onDraftChange: (s: SavedQuerySchedule) => void;
  onCancel: () => void;
  onSave: () => void;
  saving: boolean;
}

const ScheduleModal: React.FC<ScheduleModalProps> = ({ query, draft, onDraftChange, onCancel, onSave, saving }) => {
  const update = <K extends keyof SavedQuerySchedule>(k: K, v: SavedQuerySchedule[K]) =>
    onDraftChange({ ...draft, [k]: v });

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Schedule saved query"
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
      }}
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 420, maxWidth: 'calc(100vw - 32px)',
          background: 'var(--bg-surface)', border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-md)', padding: 'var(--space-4)',
          display: 'flex', flexDirection: 'column', gap: 'var(--space-3)',
          fontFamily: 'var(--font-sans)', color: 'var(--text-primary)',
        }}
      >
        <div>
          <h3 style={{ margin: 0, fontSize: 16 }}>Schedule: {query.name}</h3>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--text-tertiary)' }}>
            Runs use the gateway&apos;s in-process scheduler and execute against uploaded files. Times are UTC.
          </p>
        </div>

        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
          Interval
          <select
            value={draft.interval}
            onChange={(e) => update('interval', e.target.value as SavedQuerySchedule['interval'])}
            style={{ padding: '6px 8px', background: 'var(--bg-sunken)', border: '1px solid var(--border-default)', borderRadius: 4, color: 'var(--text-primary)' }}
          >
            <option value="hourly">Hourly</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
          </select>
        </label>

        {draft.interval !== 'hourly' && (
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
            Hour (UTC)
            <input
              type="number" min={0} max={23}
              value={draft.hour}
              onChange={(e) => update('hour', Math.max(0, Math.min(23, Number(e.target.value) || 0)))}
              style={{ padding: '6px 8px', background: 'var(--bg-sunken)', border: '1px solid var(--border-default)', borderRadius: 4, color: 'var(--text-primary)' }}
            />
          </label>
        )}

        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
          Minute
          <input
            type="number" min={0} max={59}
            value={draft.minute}
            onChange={(e) => update('minute', Math.max(0, Math.min(59, Number(e.target.value) || 0)))}
            style={{ padding: '6px 8px', background: 'var(--bg-sunken)', border: '1px solid var(--border-default)', borderRadius: 4, color: 'var(--text-primary)' }}
          />
        </label>

        {draft.interval === 'weekly' && (
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
            Day of week
            <select
              value={draft.day_of_week ?? 0}
              onChange={(e) => update('day_of_week', Number(e.target.value))}
              style={{ padding: '6px 8px', background: 'var(--bg-sunken)', border: '1px solid var(--border-default)', borderRadius: 4, color: 'var(--text-primary)' }}
            >
              {DAY_LABELS.map((lbl, i) => <option key={lbl} value={i}>{lbl}</option>)}
            </select>
          </label>
        )}

        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => update('enabled', e.target.checked)}
          />
          Enabled
        </label>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button
            onClick={onCancel}
            disabled={saving}
            style={{ padding: '6px 14px', fontSize: 12, background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 4, cursor: 'pointer' }}
          >
            Cancel
          </button>
          <button
            onClick={onSave}
            disabled={saving}
            style={{ padding: '6px 14px', fontSize: 12, background: 'var(--accent)', color: 'white', border: 'none', borderRadius: 4, cursor: saving ? 'wait' : 'pointer' }}
          >
            {saving ? 'Saving…' : 'Save schedule'}
          </button>
        </div>
      </div>
    </div>
  );
};

interface ShareModalProps {
  query: SavedQuery;
  token: string | null;
  loading: boolean;
  onClose: () => void;
  onRevoke: () => void;
}

const ShareModal: React.FC<ShareModalProps> = ({ query, token, loading, onClose, onRevoke }) => {
  const url = token ? savedQueryService.shareUrl(token) : '';
  const apiUrl = token
    ? `${(import.meta.env.VITE_API_URL || window.location.origin).replace(/\/+$/, '')}/api/v1/public/saved-queries/${encodeURIComponent(token)}`
    : '';
  const [copied, setCopied] = useState<'url' | 'api' | null>(null);

  const copy = async (text: string, kind: 'url' | 'api') => {
    try {
      await navigator.clipboard?.writeText(text);
      setCopied(kind);
      setTimeout(() => setCopied(null), 1500);
    } catch { /* clipboard blocked */ }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Share saved query"
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 520, maxWidth: 'calc(100vw - 32px)',
          background: 'var(--bg-surface)', border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-md)', padding: 'var(--space-4)',
          display: 'flex', flexDirection: 'column', gap: 'var(--space-3)',
          fontFamily: 'var(--font-sans)', color: 'var(--text-primary)',
        }}
      >
        <div>
          <h3 style={{ margin: 0, fontSize: 16 }}>Share &ldquo;{query.name}&rdquo;</h3>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--text-tertiary)' }}>
            Anyone with the link can read this query&apos;s name, prompt, and SQL. They cannot
            execute it or modify anything. Revoke at any time.
          </p>
        </div>

        {loading ? (
          <div style={{ padding: 12, fontSize: 12, color: 'var(--text-tertiary)' }}>Generating link…</div>
        ) : token ? (
          <>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
              Public link
              <div style={{ display: 'flex', gap: 6 }}>
                <input
                  readOnly
                  value={url}
                  onFocus={(e) => e.currentTarget.select()}
                  style={{ flex: 1, padding: '6px 8px', background: 'var(--bg-sunken)', border: '1px solid var(--border-default)', borderRadius: 4, color: 'var(--text-primary)', fontFamily: 'var(--font-mono)', fontSize: 11 }}
                />
                <button
                  onClick={() => copy(url, 'url')}
                  style={{ padding: '6px 10px', fontSize: 11, background: 'var(--bg-surface-2)', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 4, cursor: 'pointer' }}
                >
                  {copied === 'url' ? 'Copied!' : 'Copy'}
                </button>
              </div>
              <span style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>
                A standalone reader page is not yet wired — share the API URL below for now.
              </span>
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
              API endpoint (read-only)
              <div style={{ display: 'flex', gap: 6 }}>
                <input
                  readOnly
                  value={apiUrl}
                  onFocus={(e) => e.currentTarget.select()}
                  style={{ flex: 1, padding: '6px 8px', background: 'var(--bg-sunken)', border: '1px solid var(--border-default)', borderRadius: 4, color: 'var(--text-primary)', fontFamily: 'var(--font-mono)', fontSize: 11 }}
                />
                <button
                  onClick={() => copy(apiUrl, 'api')}
                  style={{ padding: '6px 10px', fontSize: 11, background: 'var(--bg-surface-2)', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 4, cursor: 'pointer' }}
                >
                  {copied === 'api' ? 'Copied!' : 'Copy'}
                </button>
              </div>
            </label>
          </>
        ) : (
          <div style={{ padding: 12, fontSize: 12, color: 'var(--status-error, #f87171)' }}>
            Could not generate a share link. Check your network and try again.
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
          <button
            onClick={onRevoke}
            disabled={!token}
            style={{ padding: '6px 14px', fontSize: 12, background: 'transparent', color: 'var(--red, #f87171)', border: '1px solid var(--border-default)', borderRadius: 4, cursor: token ? 'pointer' : 'not-allowed', opacity: token ? 1 : 0.5 }}
          >
            Revoke link
          </button>
          <button
            onClick={onClose}
            style={{ padding: '6px 14px', fontSize: 12, background: 'var(--accent)', color: 'white', border: 'none', borderRadius: 4, cursor: 'pointer' }}
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
};

export default Library;
