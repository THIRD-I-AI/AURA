import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  DEFAULT_WORKSPACE_ID,
  getCurrentWorkspaceId,
  setCurrentWorkspaceId,
  subscribeWorkspace,
  workspaceService,
  type Workspace,
} from '../services/api';

/**
 * Compact workspace picker for the app header.
 *
 * Shows the active workspace; opens a dropdown to switch or create a new
 * one. Changing the workspace fires listeners via ``subscribeWorkspace``
 * so pages re-fetch their scoped data.
 */
const WorkspacePicker: React.FC = () => {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [activeId, setActiveId] = useState<string>(getCurrentWorkspaceId());
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const list = await workspaceService.list();
      setWorkspaces(list);
    } catch {
      setWorkspaces([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load + listen for programmatic workspace changes.
  useEffect(() => {
    loadList();
    const unsub = subscribeWorkspace((id) => setActiveId(id));
    return unsub;
  }, [loadList]);

  // Refresh on open so freshly created workspaces from another tab show up.
  useEffect(() => {
    if (open) loadList();
  }, [open, loadList]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        setCreating(false);
        setNewName('');
        setError(null);
      }
    };
    window.addEventListener('mousedown', handler);
    return () => window.removeEventListener('mousedown', handler);
  }, [open]);

  const select = (id: string) => {
    setCurrentWorkspaceId(id);
    setOpen(false);
  };

  const create = async () => {
    const name = newName.trim();
    if (!name) {
      setError('Name is required');
      return;
    }
    setError(null);
    try {
      const created = await workspaceService.create({ name });
      await loadList();
      setCurrentWorkspaceId(created.id);
      setCreating(false);
      setNewName('');
      setOpen(false);
    } catch (e: any) {
      setError(e?.message || 'Failed to create workspace');
    }
  };

  const active = workspaces.find((w) => w.id === activeId) ?? {
    id: activeId,
    name: activeId === DEFAULT_WORKSPACE_ID ? 'Default' : activeId,
    description: null,
    created_at: '',
    updated_at: '',
  };

  return (
    <div ref={rootRef} style={{ position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Switch workspace"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 10px',
          background: 'var(--bg-elevated, #1a1d24)',
          color: 'var(--text-primary)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-md, 6px)',
          fontSize: 12,
          fontFamily: 'var(--font-sans)',
          cursor: 'pointer',
          maxWidth: 200,
        }}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
          <path d="M1.5 3h9v6a1 1 0 01-1 1h-7a1 1 0 01-1-1V3z" stroke="currentColor" strokeWidth="1.2"/>
          <path d="M4.5 3V2a.5.5 0 01.5-.5h2a.5.5 0 01.5.5v1" stroke="currentColor" strokeWidth="1.2"/>
        </svg>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {active.name}
        </span>
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
          <path d="M2 3.5l3 3 3-3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
        </svg>
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Workspaces"
          style={{
            position: 'absolute',
            top: 'calc(100% + 4px)',
            right: 0,
            minWidth: 240,
            maxWidth: 320,
            background: 'var(--bg-elevated, #1a1d24)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md, 6px)',
            boxShadow: '0 10px 30px rgba(0,0,0,0.45)',
            zIndex: 1000,
            padding: 4,
          }}
        >
          <div style={{ padding: '6px 10px', fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-tertiary)' }}>
            Workspace
          </div>
          <div style={{ maxHeight: 240, overflowY: 'auto' }}>
            {loading && workspaces.length === 0 ? (
              <div style={{ padding: 10, fontSize: 12, color: 'var(--text-tertiary)' }}>Loading…</div>
            ) : workspaces.length === 0 ? (
              <div style={{ padding: 10, fontSize: 12, color: 'var(--text-tertiary)' }}>No workspaces</div>
            ) : (
              workspaces.map((w) => {
                const selected = w.id === activeId;
                return (
                  <button
                    key={w.id}
                    role="option"
                    aria-selected={selected}
                    onClick={() => select(w.id)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      width: '100%',
                      padding: '8px 10px',
                      background: selected ? 'var(--accent-dim, rgba(99,102,241,0.12))' : 'transparent',
                      border: 'none',
                      color: 'var(--text-primary)',
                      fontFamily: 'var(--font-sans)',
                      fontSize: 12,
                      textAlign: 'left',
                      cursor: 'pointer',
                      borderRadius: 4,
                    }}
                  >
                    <span style={{ width: 6, height: 6, borderRadius: 999, background: selected ? 'var(--accent, #6366f1)' : 'transparent', border: `1px solid ${selected ? 'var(--accent, #6366f1)' : 'var(--border-default)'}` }} />
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {w.name}
                    </span>
                    {w.id === DEFAULT_WORKSPACE_ID && (
                      <span style={{ fontSize: 9, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>default</span>
                    )}
                  </button>
                );
              })
            )}
          </div>
          <div style={{ borderTop: '1px solid var(--border-subtle)', marginTop: 4, padding: 6 }}>
            {creating ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <input
                  autoFocus
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') create(); }}
                  placeholder="Workspace name"
                  aria-label="New workspace name"
                  style={{
                    padding: '6px 8px',
                    background: 'var(--bg-surface, #0f1218)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border-default)',
                    borderRadius: 4,
                    fontSize: 12,
                    fontFamily: 'var(--font-sans)',
                  }}
                />
                {error && (
                  <div style={{ fontSize: 11, color: 'var(--status-error, #f87171)' }}>{error}</div>
                )}
                <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                  <button
                    onClick={() => { setCreating(false); setNewName(''); setError(null); }}
                    style={{ padding: '4px 10px', background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 4, fontSize: 11, cursor: 'pointer' }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={create}
                    style={{ padding: '4px 10px', background: 'var(--accent, #6366f1)', color: 'white', border: 'none', borderRadius: 4, fontSize: 11, cursor: 'pointer' }}
                  >
                    Create
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => setCreating(true)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  width: '100%',
                  padding: '6px 10px',
                  background: 'transparent',
                  color: 'var(--text-secondary)',
                  border: 'none',
                  fontSize: 12,
                  fontFamily: 'var(--font-sans)',
                  textAlign: 'left',
                  cursor: 'pointer',
                  borderRadius: 4,
                }}
              >
                <span style={{ fontSize: 14, lineHeight: 1 }}>+</span>
                <span>New workspace</span>
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default WorkspacePicker;
