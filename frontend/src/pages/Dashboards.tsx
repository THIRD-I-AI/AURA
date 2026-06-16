import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  dashboardService,
  savedQueryService,
  subscribeWorkspace,
  type Dashboard,
  type DashboardRender,
  type DashboardTile,
  type DashboardTileInput,
  type RenderedTile,
  type SavedQuery,
} from '../services/api';
import { PresenceIndicator } from '../components/PresenceIndicator';

const CHART_COLORS = ['#60a5fa', '#34d399', '#fbbf24', '#f87171', '#a78bfa', '#fb923c', '#22d3ee', '#f472b6'];

const Dashboards: React.FC = () => {
  const [dashboards, setDashboards] = useState<Dashboard[]>([]);
  const [savedQueries, setSavedQueries] = useState<SavedQuery[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [render, setRender] = useState<DashboardRender | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [rendering, setRendering] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // ── New / edit modal state ─────────────────────────────────────
  const [editing, setEditing] = useState<Dashboard | null>(null);
  const [editName, setEditName] = useState<string>('');
  const [editDescription, setEditDescription] = useState<string>('');
  const [editTiles, setEditTiles] = useState<DashboardTileInput[]>([]);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [dash, sq] = await Promise.all([
        dashboardService.list(),
        savedQueryService.list(),
      ]);
      setDashboards(dash);
      setSavedQueries(sq);
      if (!selectedId && dash.length > 0) setSelectedId(dash[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboards');
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => { refreshAll(); }, [refreshAll]);

  // On workspace switch, drop any selection from the prior tenancy
  // and re-fetch — dashboards are workspace-scoped server-side.
  useEffect(() => subscribeWorkspace(() => {
    setSelectedId(null);
    setRender(null);
    refreshAll();
  }), [refreshAll]);

  const selected = useMemo(
    () => dashboards.find((d) => d.id === selectedId) ?? null,
    [dashboards, selectedId],
  );

  const renderSelected = useCallback(async () => {
    if (!selected) return;
    setRendering(true);
    setError(null);
    try {
      const r = await dashboardService.render(selected.id);
      setRender(r);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to render dashboard');
    } finally {
      setRendering(false);
    }
  }, [selected]);

  // Auto-render when selection changes
  useEffect(() => {
    if (selected) {
      setRender(null);
      renderSelected();
    }
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const openCreate = () => {
    setEditing({ id: '', name: '', description: null, tiles: [], created_at: '', updated_at: '' });
    setEditName('');
    setEditDescription('');
    setEditTiles([]);
  };

  const openEdit = (d: Dashboard) => {
    setEditing(d);
    setEditName(d.name);
    setEditDescription(d.description ?? '');
    setEditTiles(d.tiles.map((t) => ({
      saved_query_id: t.saved_query_id,
      title: t.title ?? undefined,
      chart_type: t.chart_type,
    })));
  };

  const commitEdit = async () => {
    const name = editName.trim();
    if (!name) { setError('Name is required'); return; }
    try {
      if (!editing || !editing.id) {
        const created = await dashboardService.create({
          name,
          description: editDescription.trim() || undefined,
          tiles: editTiles,
        });
        setDashboards((prev) => [created, ...prev]);
        setSelectedId(created.id);
      } else {
        const updated = await dashboardService.update(editing.id, {
          name,
          description: editDescription.trim(),
          tiles: editTiles,
        });
        setDashboards((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));
        if (selectedId === updated.id) setRender(null);
      }
      setEditing(null);
      setTimeout(renderSelected, 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save dashboard');
    }
  };

  const onDelete = async (d: Dashboard) => {
    if (!window.confirm(`Delete dashboard "${d.name}"?`)) return;
    try {
      await dashboardService.remove(d.id);
      setDashboards((prev) => prev.filter((x) => x.id !== d.id));
      if (selectedId === d.id) { setSelectedId(null); setRender(null); }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete dashboard');
    }
  };

  // ── Tile draft helpers ─────────────────────────────────────────
  const addTile = () => {
    if (savedQueries.length === 0) return;
    setEditTiles((prev) => [...prev, { saved_query_id: savedQueries[0].id, chart_type: 'table' }]);
  };
  const moveTile = (idx: number, dir: -1 | 1) => {
    setEditTiles((prev) => {
      const next = [...prev];
      const target = idx + dir;
      if (target < 0 || target >= next.length) return prev;
      [next[idx], next[target]] = [next[target], next[idx]];
      return next;
    });
  };
  const removeTile = (idx: number) => {
    setEditTiles((prev) => prev.filter((_, i) => i !== idx));
  };
  const updateTile = (idx: number, patch: Partial<DashboardTileInput>) => {
    setEditTiles((prev) => prev.map((t, i) => (i === idx ? { ...t, ...patch } : t)));
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
        <button
          onClick={openCreate}
          style={{ padding: '6px 14px', fontSize: 13, fontWeight: 500, background: 'var(--accent)', color: 'white', border: 'none', borderRadius: 'var(--radius-sm)', cursor: 'pointer' }}
        >
          + New dashboard
        </button>
        <button
          onClick={refreshAll}
          disabled={loading}
          style={{ padding: '6px 14px', fontSize: 13, background: 'var(--bg-surface-2)', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', cursor: loading ? 'wait' : 'pointer' }}
        >
          {loading ? 'Loading…' : 'Refresh list'}
        </button>
        {selected && (
          <button
            onClick={renderSelected}
            disabled={rendering}
            style={{ padding: '6px 14px', fontSize: 13, background: 'var(--bg-surface-2)', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', cursor: rendering ? 'wait' : 'pointer' }}
          >
            {rendering ? 'Running tiles…' : 'Re-run tiles'}
          </button>
        )}
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
          {dashboards.length} dashboards · {savedQueries.length} saved queries
        </span>
      </div>

      {error && (
        <div role="alert" style={{ padding: 'var(--space-3)', background: 'rgba(239,68,68,0.08)', border: '1px solid #f87171', borderRadius: 'var(--radius-md)', color: '#f87171', fontSize: 'var(--font-sm)' }}>
          {error}
        </div>
      )}

      <div className="aura-split aura-split--aside">
        {/* Dashboard list */}
        <aside style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
          {dashboards.length === 0 ? (
            <div style={{ padding: 'var(--space-4)', border: '1px dashed var(--border-default)', borderRadius: 'var(--radius-md)', fontSize: 13, color: 'var(--text-tertiary)', textAlign: 'center' }}>
              No dashboards yet.
            </div>
          ) : dashboards.map((d) => (
            <button
              key={d.id}
              onClick={() => setSelectedId(d.id)}
              style={{
                textAlign: 'left',
                padding: 'var(--space-3)',
                background: selectedId === d.id ? 'var(--bg-surface-2)' : 'var(--bg-surface)',
                border: '1px solid ' + (selectedId === d.id ? 'var(--accent)' : 'var(--border-subtle)'),
                borderRadius: 'var(--radius-md)',
                color: 'var(--text-primary)',
                cursor: 'pointer',
                fontFamily: 'var(--font-sans)',
              }}
            >
              <div style={{ fontSize: 14, fontWeight: 600 }}>{d.name}</div>
              {d.description && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{d.description}</div>
              )}
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
                {d.tiles.length} tile{d.tiles.length === 1 ? '' : 's'} · updated {new Date(d.updated_at).toLocaleString()}
              </div>
            </button>
          ))}
        </aside>

        {/* Selected dashboard */}
        <section>
          {!selected ? (
            <div style={{ padding: 'var(--space-8)', textAlign: 'center', color: 'var(--text-tertiary)', border: '1px dashed var(--border-default)', borderRadius: 'var(--radius-md)' }}>
              Select or create a dashboard to get started.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
                <h3 style={{ margin: 0, fontSize: 18, color: 'var(--text-primary)' }}>{selected.name}</h3>
                <button
                  onClick={() => openEdit(selected)}
                  style={{ padding: '4px 10px', fontSize: 12, background: 'var(--bg-surface-2)', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', cursor: 'pointer' }}
                >
                  Edit
                </button>
                <button
                  onClick={() => onDelete(selected)}
                  style={{ padding: '4px 10px', fontSize: 12, background: 'transparent', color: '#f87171', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', cursor: 'pointer' }}
                >
                  Delete
                </button>
                <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
                  <PresenceIndicator room={`dashboard:${selected.id}`} />
                  {render?.rendered_at && (
                    <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                      Rendered {new Date(render.rendered_at).toLocaleTimeString()}
                    </span>
                  )}
                </div>
              </div>
              {selected.description && (
                <p style={{ margin: 0, fontSize: 13, color: 'var(--text-secondary)' }}>{selected.description}</p>
              )}

              {selected.tiles.length === 0 ? (
                <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-tertiary)', border: '1px dashed var(--border-default)', borderRadius: 'var(--radius-md)' }}>
                  This dashboard has no tiles yet. Click <strong>Edit</strong> to add one.
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 'var(--space-3)' }}>
                  {selected.tiles.map((tile) => {
                    const r = render?.tiles.find((x) => x.tile_id === tile.id);
                    return <TileCard key={tile.id} tile={tile} render={r} rendering={rendering} />;
                  })}
                </div>
              )}
            </div>
          )}
        </section>
      </div>

      {editing && (
        <EditModal
          name={editName}
          description={editDescription}
          tiles={editTiles}
          savedQueries={savedQueries}
          isNew={!editing.id}
          onNameChange={setEditName}
          onDescriptionChange={setEditDescription}
          onAddTile={addTile}
          onMoveTile={moveTile}
          onRemoveTile={removeTile}
          onUpdateTile={updateTile}
          onCancel={() => setEditing(null)}
          onSave={commitEdit}
        />
      )}
    </div>
  );
};

// ── Tile card (table + auto-chart by chart_type) ─────────────────────

const TileCard: React.FC<{ tile: DashboardTile; render?: RenderedTile; rendering: boolean }> = ({ tile, render, rendering }) => {
  const title = tile.title || render?.title || '(untitled)';
  return (
    <div style={{
      padding: 'var(--space-3) var(--space-4)',
      background: 'var(--bg-surface)',
      border: '1px solid var(--border-subtle)',
      borderRadius: 'var(--radius-md)',
      display: 'flex', flexDirection: 'column', gap: 'var(--space-2)',
      minHeight: 220,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'space-between' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{title}</div>
        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{tile.chart_type}</span>
      </div>
      {!render ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
          {rendering ? 'Running…' : 'Waiting to render…'}
        </div>
      ) : render.status !== 'success' ? (
        <div style={{ flex: 1, padding: 8, background: 'rgba(239,68,68,0.08)', borderRadius: 4, color: '#fca5a5', fontSize: 12 }}>
          {render.error || 'Error rendering tile'}
        </div>
      ) : (
        <>
          <TileBody render={render} chartType={tile.chart_type} />
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', display: 'flex', gap: 8 }}>
            <span>{render.row_count} rows</span>
            <span>·</span>
            <span>{render.execution_time_ms} ms</span>
          </div>
        </>
      )}
    </div>
  );
};

const TileBody: React.FC<{ render: RenderedTile; chartType: string }> = ({ render, chartType }) => {
  const { columns, rows } = render;
  if (rows.length === 0) {
    return <div style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>No rows returned.</div>;
  }

  if (chartType === 'kpi') {
    const first = rows[0];
    const value = typeof first[0] === 'number' ? (first[0] as number).toLocaleString() : String(first[0] ?? '—');
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 8 }}>
        <div style={{ fontSize: 32, fontWeight: 700, color: '#60a5fa' }}>{value}</div>
        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{columns[0]}</div>
      </div>
    );
  }

  if ((chartType === 'bar' || chartType === 'line' || chartType === 'pie') && columns.length >= 2) {
    const data = rows.slice(0, 30).map((r) => ({
      name: String(r[0] ?? ''),
      value: Number(r[1] ?? 0),
    })).filter((d) => !Number.isNaN(d.value));
    if (data.length === 0) return <TableView columns={columns} rows={rows} />;
    if (chartType === 'bar') {
      return (
        <div style={{ flex: 1, minHeight: 160 }}>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="name" stroke="#94a3b8" fontSize={10} interval={0} angle={-15} textAnchor="end" height={40} />
              <YAxis stroke="#94a3b8" fontSize={10} />
              <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #1f2937', fontSize: 12 }} />
              <Bar dataKey="value" fill="#60a5fa" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      );
    }
    if (chartType === 'line') {
      return (
        <div style={{ flex: 1, minHeight: 160 }}>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="name" stroke="#94a3b8" fontSize={10} />
              <YAxis stroke="#94a3b8" fontSize={10} />
              <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #1f2937', fontSize: 12 }} />
              <Line type="monotone" dataKey="value" stroke="#34d399" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      );
    }
    return (
      <div style={{ flex: 1, minHeight: 160 }}>
        <ResponsiveContainer width="100%" height={180}>
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="name" outerRadius={70} label={{ fontSize: 10 }}>
              {data.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
            </Pie>
            <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #1f2937', fontSize: 12 }} />
          </PieChart>
        </ResponsiveContainer>
      </div>
    );
  }

  return <TableView columns={columns} rows={rows} />;
};

const TableView: React.FC<{ columns: string[]; rows: Array<Array<unknown>> }> = ({ columns, rows }) => (
  <div style={{ flex: 1, overflow: 'auto', maxHeight: 200, border: '1px solid var(--border-subtle)', borderRadius: 4 }}>
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
      <thead>
        <tr style={{ background: 'var(--bg-sunken, #0b1220)' }}>
          {columns.map((c) => (
            <th key={c} style={{ padding: '4px 8px', textAlign: 'left', fontWeight: 600, color: 'var(--text-secondary)' }}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 50).map((r, i) => (
          <tr key={i} style={{ borderTop: '1px solid var(--border-subtle)' }}>
            {r.map((v, j) => (
              <td key={j} style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                {v === null ? <span style={{ color: 'var(--text-tertiary)' }}>null</span> : String(v)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

// ── Edit modal ─────────────────────────────────────────────────────

interface EditModalProps {
  name: string;
  description: string;
  tiles: DashboardTileInput[];
  savedQueries: SavedQuery[];
  isNew: boolean;
  onNameChange: (v: string) => void;
  onDescriptionChange: (v: string) => void;
  onAddTile: () => void;
  onMoveTile: (idx: number, dir: -1 | 1) => void;
  onRemoveTile: (idx: number) => void;
  onUpdateTile: (idx: number, patch: Partial<DashboardTileInput>) => void;
  onCancel: () => void;
  onSave: () => void;
}

const EditModal: React.FC<EditModalProps> = (props) => (
  <div
    role="dialog"
    aria-modal="true"
    aria-label="Edit dashboard"
    style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}
    onClick={props.onCancel}
  >
    <div
      onClick={(e) => e.stopPropagation()}
      style={{
        width: 640, maxWidth: 'calc(100vw - 32px)', maxHeight: 'calc(100vh - 64px)', overflow: 'auto',
        background: 'var(--bg-surface)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)',
        padding: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 'var(--space-3)',
        fontFamily: 'var(--font-sans)', color: 'var(--text-primary)',
      }}
    >
      <h3 style={{ margin: 0, fontSize: 16 }}>{props.isNew ? 'New dashboard' : 'Edit dashboard'}</h3>

      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Name
        <input
          value={props.name}
          onChange={(e) => props.onNameChange(e.target.value)}
          autoFocus
          style={{ padding: '6px 8px', background: 'var(--bg-sunken)', border: '1px solid var(--border-default)', borderRadius: 4, color: 'var(--text-primary)' }}
        />
      </label>

      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Description (optional)
        <input
          value={props.description}
          onChange={(e) => props.onDescriptionChange(e.target.value)}
          style={{ padding: '6px 8px', background: 'var(--bg-sunken)', border: '1px solid var(--border-default)', borderRadius: 4, color: 'var(--text-primary)' }}
        />
      </label>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>Tiles</div>
        <button
          type="button"
          onClick={props.onAddTile}
          disabled={props.savedQueries.length === 0}
          style={{ padding: '4px 10px', fontSize: 12, background: 'var(--bg-surface-2)', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 4, cursor: props.savedQueries.length === 0 ? 'not-allowed' : 'pointer' }}
        >
          + Add tile
        </button>
      </div>

      {props.savedQueries.length === 0 && (
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
          No saved queries — create some in the Library page first.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {props.tiles.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>No tiles yet.</div>
        ) : props.tiles.map((t, i) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 120px auto', gap: 6, alignItems: 'center', padding: 6, border: '1px solid var(--border-subtle)', borderRadius: 4 }}>
            <select
              value={t.saved_query_id}
              onChange={(e) => props.onUpdateTile(i, { saved_query_id: e.target.value })}
              style={{ padding: '4px 6px', background: 'var(--bg-sunken)', border: '1px solid var(--border-default)', borderRadius: 4, color: 'var(--text-primary)', fontSize: 12 }}
            >
              {props.savedQueries.map((sq) => (
                <option key={sq.id} value={sq.id}>{sq.name}</option>
              ))}
            </select>
            <select
              value={t.chart_type || 'table'}
              onChange={(e) => props.onUpdateTile(i, { chart_type: e.target.value })}
              style={{ padding: '4px 6px', background: 'var(--bg-sunken)', border: '1px solid var(--border-default)', borderRadius: 4, color: 'var(--text-primary)', fontSize: 12 }}
            >
              <option value="table">Table</option>
              <option value="bar">Bar</option>
              <option value="line">Line</option>
              <option value="pie">Pie</option>
              <option value="kpi">KPI</option>
            </select>
            <div style={{ display: 'flex', gap: 2 }}>
              <button type="button" onClick={() => props.onMoveTile(i, -1)} style={iconBtnStyle} aria-label="Move up">↑</button>
              <button type="button" onClick={() => props.onMoveTile(i, 1)} style={iconBtnStyle} aria-label="Move down">↓</button>
              <button type="button" onClick={() => props.onRemoveTile(i)} style={{ ...iconBtnStyle, color: '#f87171' }} aria-label="Remove tile">×</button>
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
        <button
          type="button"
          onClick={props.onCancel}
          style={{ padding: '6px 14px', fontSize: 12, background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 4, cursor: 'pointer' }}
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={props.onSave}
          style={{ padding: '6px 14px', fontSize: 12, background: 'var(--accent)', color: 'white', border: 'none', borderRadius: 4, cursor: 'pointer' }}
        >
          {props.isNew ? 'Create' : 'Save'}
        </button>
      </div>
    </div>
  </div>
);

const iconBtnStyle: React.CSSProperties = {
  width: 26, height: 26,
  background: 'var(--bg-surface-2)',
  color: 'var(--text-secondary)',
  border: '1px solid var(--border-default)',
  borderRadius: 4,
  cursor: 'pointer',
  fontSize: 13,
};

export default Dashboards;
