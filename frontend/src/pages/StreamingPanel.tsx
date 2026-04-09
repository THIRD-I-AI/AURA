import React, { useState, useEffect, useRef, useCallback } from 'react';
import { type PageType } from '../components/Layout/AppLayout';
import {
  streamingService,
  type StreamPipelineDef,
  type StreamPipelineMetrics,
  type StreamTemplate,
  type StreamingSchemas,
  type SchemaField,
} from '../services/api';
import StreamingCreateForm from './streaming/StreamingCreateForm';
import './StreamingPanel.css';

/* ================================================================
   Types
   ================================================================ */

type TabType = 'pipelines' | 'templates' | 'create';

interface StreamingPanelProps {
  setCurrentPage?: (page: PageType) => void;
}

/* ================================================================
   Helpers
   ================================================================ */

export const STATUS_COLORS: Record<string, string> = {
  running: '#22c55e',
  paused:  '#f59e0b',
  stopped: '#6b7280',
  failed:  '#ef4444',
  draft:   '#8b5cf6',
  starting: '#3b82f6',
  stopping: '#f59e0b',
};

export const STATUS_LABELS: Record<string, string> = {
  running:  'Running',
  paused:   'Paused',
  stopped:  'Stopped',
  failed:   'Failed',
  draft:    'Draft',
  starting: 'Starting…',
  stopping: 'Stopping…',
};

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function formatEpoch(epoch: number): string {
  if (!epoch) return '—';
  return new Date(epoch * 1000).toLocaleTimeString();
}

function defaultsFromFields(fields: SchemaField[]): Record<string, any> {
  const out: Record<string, any> = {};
  for (const f of fields) {
    if (f.default !== undefined) out[f.key] = f.default;
  }
  return out;
}

/* ================================================================
   Main Component
   ================================================================ */

const StreamingPanel: React.FC<StreamingPanelProps> = () => {
  const [activeTab, setActiveTab] = useState<TabType>('pipelines');
  const [pipelines, setPipelines] = useState<StreamPipelineDef[]>([]);
  const [templates, setTemplates] = useState<StreamTemplate[]>([]);
  const [schemas, setSchemas] = useState<StreamingSchemas | null>(null);
  const [selectedPipeline, setSelectedPipeline] = useState<string | null>(null);
  const [liveMetrics, setLiveMetrics] = useState<StreamPipelineMetrics | null>(null);
  const [sseEvents, setSseEvents] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // ── Create form state ──
  const [formName, setFormName] = useState('');
  const [formDesc, setFormDesc] = useState('');
  const [formSourceType, setFormSourceType] = useState('');
  const [formSourceConfig, setFormSourceConfig] = useState<Record<string, any>>({});
  const [formWindowType, setFormWindowType] = useState('tumbling');
  const [formWindowConfig, setFormWindowConfig] = useState<Record<string, any>>({});
  const [formSinks, setFormSinks] = useState<{ type: string; config: Record<string, any> }[]>([]);
  const [formTransforms, setFormTransforms] = useState<{ type: string; description: string; config: Record<string, any> }[]>([]);
  const [formEventTimeField, setFormEventTimeField] = useState('timestamp');
  const [formWatermarkDelay, setFormWatermarkDelay] = useState(10);
  const [formCheckpointInterval, setFormCheckpointInterval] = useState(30);

  const sseRef = useRef<EventSource | null>(null);
  const metricsTimerRef = useRef<number | null>(null);

  // ── Fetch ──
  const fetchPipelines = useCallback(async () => {
    try {
      const data = await streamingService.list();
      setPipelines(data.pipelines || []);
    } catch { /* backend may be down */ }
  }, []);

  const fetchTemplates = useCallback(async () => {
    try {
      const data = await streamingService.templates();
      setTemplates(data.templates || []);
    } catch { /* */ }
  }, []);

  const fetchSchemas = useCallback(async () => {
    try {
      const data = await streamingService.schemas();
      setSchemas(data);
      if (data.sources) {
        const first = Object.entries(data.sources).find(([, s]) => s.implemented);
        if (first) {
          setFormSourceType(first[0]);
          setFormSourceConfig(defaultsFromFields(first[1].fields));
        }
      }
      if (data.windows?.tumbling) setFormWindowConfig(defaultsFromFields(data.windows.tumbling.fields));
    } catch { /* */ }
  }, []);

  useEffect(() => {
    fetchPipelines();
    fetchTemplates();
    fetchSchemas();
  }, [fetchPipelines, fetchTemplates, fetchSchemas]);

  // ── Toast ──
  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  // ── SSE ──
  const connectSSE = useCallback((pipelineId: string) => {
    if (sseRef.current) sseRef.current.close();
    const es = new EventSource(streamingService.streamUrl(pipelineId));
    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'metrics') setLiveMetrics(data as StreamPipelineMetrics);
        else setSseEvents((prev) => [data, ...prev].slice(0, 100));
      } catch { /* ignore */ }
    };
    sseRef.current = es;
  }, []);

  const disconnectSSE = useCallback(() => {
    if (sseRef.current) { sseRef.current.close(); sseRef.current = null; }
  }, []);

  useEffect(() => {
    if (!selectedPipeline) return;
    const poll = async () => {
      try { setLiveMetrics(await streamingService.metrics(selectedPipeline)); } catch { /* */ }
    };
    poll();
    metricsTimerRef.current = window.setInterval(poll, 2000);
    return () => { if (metricsTimerRef.current) clearInterval(metricsTimerRef.current); };
  }, [selectedPipeline]);

  useEffect(() => () => disconnectSSE(), [disconnectSSE]);

  // ── Lifecycle actions ──
  const handleStart = async (id: string) => {
    try { setIsLoading(true); await streamingService.start(id); showToast('Pipeline started'); connectSSE(id); await fetchPipelines(); }
    catch (err: any) { setError(err?.message || 'Failed to start'); } finally { setIsLoading(false); }
  };
  const handleStop = async (id: string) => {
    try { setIsLoading(true); await streamingService.stop(id); showToast('Pipeline stopped'); disconnectSSE(); await fetchPipelines(); }
    catch (err: any) { setError(err?.message || 'Failed to stop'); } finally { setIsLoading(false); }
  };
  const handlePause = async (id: string) => {
    try { await streamingService.pause(id); showToast('Pipeline paused'); await fetchPipelines(); }
    catch (err: any) { setError(err?.message || 'Failed to pause'); }
  };
  const handleResume = async (id: string) => {
    try { await streamingService.resume(id); showToast('Pipeline resumed'); await fetchPipelines(); }
    catch (err: any) { setError(err?.message || 'Failed to resume'); }
  };
  const handleDelete = async (id: string) => {
    try {
      await streamingService.remove(id);
      showToast('Pipeline deleted');
      if (selectedPipeline === id) { setSelectedPipeline(null); setLiveMetrics(null); disconnectSSE(); }
      await fetchPipelines();
    } catch (err: any) { setError(err?.message || 'Failed to delete'); }
  };
  const handleSelect = (id: string) => {
    setSelectedPipeline(id);
    setSseEvents([]);
    setLiveMetrics(null);
    const pipe = pipelines.find((p) => p.id === id);
    if (pipe?.status === 'running') connectSSE(id); else disconnectSSE();
  };
  const handleCreateFromTemplate = async (tmpl: StreamTemplate) => {
    try {
      setIsLoading(true);
      const created = await streamingService.create(tmpl.pipeline);
      showToast(`Pipeline "${created.name}" created`);
      setActiveTab('pipelines');
      await fetchPipelines();
    } catch (err: any) { setError(err?.message || 'Failed to create'); } finally { setIsLoading(false); }
  };

  // ── Form handlers ──
  const handleSourceTypeChange = (newType: string) => {
    setFormSourceType(newType);
    setFormSourceConfig(schemas?.sources[newType] ? defaultsFromFields(schemas.sources[newType].fields) : {});
  };
  const handleWindowTypeChange = (newType: string) => {
    setFormWindowType(newType);
    setFormWindowConfig(schemas?.windows[newType] ? defaultsFromFields(schemas.windows[newType].fields) : {});
  };
  const handleAddSink = (sinkType: string) => {
    if (formSinks.some((s) => s.type === sinkType)) return;
    const cfg = schemas?.sinks[sinkType] ? defaultsFromFields(schemas.sinks[sinkType].fields) : {};
    setFormSinks([...formSinks, { type: sinkType, config: cfg }]);
  };
  const handleRemoveSink = (idx: number) => setFormSinks(formSinks.filter((_, i) => i !== idx));
  const handleSinkConfigChange = (idx: number, key: string, value: any) =>
    setFormSinks(formSinks.map((s, i) => i === idx ? { ...s, config: { ...s.config, [key]: value } } : s));
  const handleAddTransform = (tType: string) => {
    const tSchema = schemas?.transforms[tType];
    const cfg: Record<string, any> = {};
    if (tSchema) for (const f of tSchema.fields) {
      if (f.type === 'agg_fields') cfg[f.key] = [];
      else if (f.default !== undefined) cfg[f.key] = f.default;
      else cfg[f.key] = '';
    }
    setFormTransforms([...formTransforms, { type: tType, description: tSchema?.label || tType, config: cfg }]);
  };
  const handleRemoveTransform = (idx: number) => setFormTransforms(formTransforms.filter((_, i) => i !== idx));
  const handleTransformConfigChange = (idx: number, key: string, value: any) =>
    setFormTransforms(formTransforms.map((t, i) => i === idx ? { ...t, config: { ...t.config, [key]: value } } : t));

  const handleCreatePipeline = async () => {
    if (!formName.trim()) { setError('Pipeline name is required'); return; }
    if (!formSourceType) { setError('Select a source type'); return; }
    const sinkPayload = [...formSinks];
    if (!sinkPayload.some((s) => s.type === 'sse')) sinkPayload.push({ type: 'sse', config: {} });
    const payload = {
      name: formName.trim(),
      description: formDesc.trim(),
      source: { type: formSourceType, config: formSourceConfig },
      event_time_field: formEventTimeField,
      watermark_delay_seconds: formWatermarkDelay,
      window: { type: formWindowType, ...formWindowConfig },
      transforms: formTransforms,
      sinks: sinkPayload,
      checkpoint_interval_seconds: formCheckpointInterval,
    };
    try {
      setIsLoading(true);
      const created = await streamingService.create(payload);
      showToast(`Pipeline "${created.name}" created`);
      setFormName(''); setFormDesc(''); setFormSinks([]); setFormTransforms([]);
      setActiveTab('pipelines');
      await fetchPipelines();
    } catch (err: any) { setError(err?.message || 'Failed to create pipeline'); } finally { setIsLoading(false); }
  };

  // ── Derived ──
  const selected = pipelines.find((p) => p.id === selectedPipeline);

  // ── Render ──
  return (
    <div className="streaming-panel">
      {toast && <div className="streaming-toast">{toast}</div>}
      {error && (
        <div className="streaming-error">
          <span>{error}</span>
          <button onClick={() => setError(null)}>&times;</button>
        </div>
      )}

      {/* Tabs */}
      <div className="streaming-tabs">
        {([
          { key: 'pipelines' as TabType, label: 'My Pipelines' },
          { key: 'create'    as TabType, label: '+ Create' },
          { key: 'templates' as TabType, label: 'Templates' },
        ]).map(({ key, label }) => (
          <button key={key}
            className={`streaming-tab ${activeTab === key ? 'streaming-tab--active' : ''}`}
            onClick={() => setActiveTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {/* ── Create Tab ── */}
      {activeTab === 'create' && schemas && (
        <StreamingCreateForm
          schemas={schemas}
          isLoading={isLoading}
          formName={formName}           setFormName={setFormName}
          formDesc={formDesc}           setFormDesc={setFormDesc}
          formSourceType={formSourceType}
          formSourceConfig={formSourceConfig} setFormSourceConfig={setFormSourceConfig}
          formWindowType={formWindowType}
          formWindowConfig={formWindowConfig} setFormWindowConfig={setFormWindowConfig}
          formEventTimeField={formEventTimeField} setFormEventTimeField={setFormEventTimeField}
          formWatermarkDelay={formWatermarkDelay} setFormWatermarkDelay={setFormWatermarkDelay}
          formCheckpointInterval={formCheckpointInterval} setFormCheckpointInterval={setFormCheckpointInterval}
          formSinks={formSinks}
          formTransforms={formTransforms}
          onSourceTypeChange={handleSourceTypeChange}
          onWindowTypeChange={handleWindowTypeChange}
          onAddSink={handleAddSink}           onRemoveSink={handleRemoveSink}
          onSinkConfigChange={handleSinkConfigChange}
          onAddTransform={handleAddTransform} onRemoveTransform={handleRemoveTransform}
          onTransformConfigChange={handleTransformConfigChange}
          onSubmit={handleCreatePipeline}
          onCancel={() => setActiveTab('pipelines')}
        />
      )}
      {activeTab === 'create' && !schemas && (
        <div className="streaming-empty">Loading pipeline configuration schemas… Is the backend running?</div>
      )}

      {/* ── Templates Tab ── */}
      {activeTab === 'templates' && (
        <div className="streaming-templates">
          <p className="sf-help-block" style={{ marginBottom: '1rem' }}>
            Templates are pre-configured starting points. Create one, then customize it.
          </p>
          {templates.length === 0 && (
            <div className="streaming-empty">No templates available — is the backend running?</div>
          )}
          <div className="streaming-template-grid">
            {templates.map((tmpl) => (
              <div key={tmpl.id} className="streaming-template-card">
                <div className="streaming-template-header">
                  <h3>{tmpl.name}</h3>
                  <div className="streaming-template-tags">
                    {tmpl.tags.map((t) => <span key={t} className="streaming-tag">{t}</span>)}
                  </div>
                </div>
                <p className="streaming-template-desc">{tmpl.description}</p>
                <div className="streaming-template-meta">
                  <span>Source: {tmpl.pipeline.source?.type}</span>
                  <span>Window: {tmpl.pipeline.window?.type} ({tmpl.pipeline.window?.size_seconds || tmpl.pipeline.window?.gap_seconds}s)</span>
                </div>
                <button className="streaming-btn streaming-btn--primary"
                  onClick={() => handleCreateFromTemplate(tmpl)} disabled={isLoading}>
                  + Create Pipeline
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Pipelines Tab ── */}
      {activeTab === 'pipelines' && (() => {
        const runningCount = pipelines.filter((p) => p.status === 'running').length;
        const pausedCount  = pipelines.filter((p) => p.status === 'paused').length;
        const failedCount  = pipelines.filter((p) => p.status === 'failed').length;
        return (
          <>
            <div className="streaming-kpi-bar">
              {[
                { label: 'Total Pipelines', value: pipelines.length, sub: 'all configured', color: '' },
                { label: 'Running', value: runningCount, sub: 'active pipelines', color: runningCount > 0 ? '#34d399' : '' },
                { label: 'Paused',  value: pausedCount,  sub: 'suspended',        color: pausedCount  > 0 ? '#fbbf24' : '' },
                { label: 'Failed',  value: failedCount,  sub: 'need attention',   color: failedCount  > 0 ? '#f87171' : '' },
              ].map(({ label, value, sub, color }) => (
                <div key={label} className="streaming-kpi-card">
                  <span className="streaming-kpi-card__label">{label}</span>
                  <span className="streaming-kpi-card__value" style={color ? { color } : {}}>{value}</span>
                  <span className="streaming-kpi-card__sub">{sub}</span>
                </div>
              ))}
            </div>

            <div className="streaming-content">
              {/* Left: pipeline list */}
              <div className="streaming-list">
                {pipelines.length === 0 && (
                  <div className="streaming-empty">
                    <p>No streaming pipelines yet.</p>
                    <button className="streaming-btn streaming-btn--primary" onClick={() => setActiveTab('create')}>
                      + Create Pipeline
                    </button>
                  </div>
                )}
                {pipelines.map((pipe) => (
                  <div key={pipe.id}
                    className={`streaming-pipeline-card ${selectedPipeline === pipe.id ? 'streaming-pipeline-card--selected' : ''}`}
                    onClick={() => handleSelect(pipe.id)}>
                    <div className="streaming-pipeline-header">
                      <h3>{pipe.name}</h3>
                      <span className="streaming-status-badge" style={{ background: STATUS_COLORS[pipe.status] || '#6b7280' }}>
                        {STATUS_LABELS[pipe.status] || pipe.status}
                      </span>
                    </div>
                    <p className="streaming-pipeline-desc">{pipe.description || '—'}</p>
                    <div className="streaming-pipeline-meta">
                      <span>In: {pipe.source.type}</span>
                      <span>Win: {pipe.window.type} ({pipe.window.size_seconds || pipe.window.gap_seconds}s)</span>
                      <span>Out: {pipe.sinks.map((s) => s.type).join(', ')}</span>
                    </div>
                    <div className="streaming-pipeline-actions">
                      {(pipe.status === 'draft' || pipe.status === 'stopped' || pipe.status === 'failed') && (
                        <button className="streaming-btn streaming-btn--success"
                          onClick={(e) => { e.stopPropagation(); handleStart(pipe.id); }} disabled={isLoading}>
                          ▶ Start
                        </button>
                      )}
                      {pipe.status === 'running' && (<>
                        <button className="streaming-btn streaming-btn--warning"
                          onClick={(e) => { e.stopPropagation(); handlePause(pipe.id); }}>⏸ Pause</button>
                        <button className="streaming-btn streaming-btn--danger"
                          onClick={(e) => { e.stopPropagation(); handleStop(pipe.id); }}>⏹ Stop</button>
                      </>)}
                      {pipe.status === 'paused' && (
                        <button className="streaming-btn streaming-btn--success"
                          onClick={(e) => { e.stopPropagation(); handleResume(pipe.id); }}>▶ Resume</button>
                      )}
                      {pipe.status !== 'running' && pipe.status !== 'starting' && (
                        <button className="streaming-btn streaming-btn--ghost"
                          onClick={(e) => { e.stopPropagation(); handleDelete(pipe.id); }}>Delete</button>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {/* Right: detail panel */}
              {selected ? (
                <div className="streaming-detail">
                  <h2>{selected.name}</h2>
                  <div className="streaming-kpi-grid">
                    {[
                      { label: 'Events In',      value: liveMetrics?.events_in?.toLocaleString() },
                      { label: 'Events Out',     value: liveMetrics?.events_out?.toLocaleString() },
                      { label: 'Events/sec',     value: liveMetrics?.events_per_second },
                      { label: 'Late Events',    value: liveMetrics?.events_late, warn: true },
                      { label: 'Active Windows', value: liveMetrics?.active_windows },
                      { label: 'Closed Windows', value: liveMetrics?.closed_windows },
                      { label: 'Watermark',      value: formatEpoch(liveMetrics?.watermark_position || 0) },
                      { label: 'Uptime',         value: formatUptime(liveMetrics?.uptime_seconds || 0) },
                    ].map(({ label, value, warn }) => (
                      <div key={label} className="streaming-kpi">
                        <span className="streaming-kpi-label">{label}</span>
                        <span className={`streaming-kpi-value${warn ? ' streaming-kpi-value--warn' : ''}`}>
                          {value ?? '—'}
                        </span>
                      </div>
                    ))}
                  </div>
                  {liveMetrics?.errors && liveMetrics.errors.length > 0 && (
                    <div className="streaming-errors-box">
                      <h4>Errors</h4>
                      {liveMetrics.errors.map((e, i) => <div key={i} className="streaming-error-line">{e}</div>)}
                    </div>
                  )}
                  <div className="streaming-config-summary">
                    <h4>Configuration</h4>
                    <div className="streaming-config-grid">
                      <div><strong>Source</strong><br />{selected.source.type} — {JSON.stringify(selected.source.config)}</div>
                      <div><strong>Window</strong><br />{selected.window.type} ({selected.window.size_seconds || selected.window.gap_seconds}s) — late: {selected.window.late_data_policy}</div>
                      <div><strong>Transforms</strong><br />{selected.transforms.length > 0 ? selected.transforms.map((t) => `${t.type}: ${t.description}`).join(' → ') : 'None'}</div>
                      <div><strong>Sinks</strong><br />{selected.sinks.map((s) => s.type).join(', ')}</div>
                      <div><strong>Checkpoint</strong><br />Every {selected.checkpoint_interval_seconds}s</div>
                      <div><strong>Watermark Delay</strong><br />{selected.watermark_delay_seconds}s</div>
                    </div>
                  </div>
                  <div className="streaming-event-feed">
                    <h4>Live Events ({sseEvents.length})</h4>
                    <div className="streaming-event-list">
                      {sseEvents.length === 0 ? (
                        <div className="streaming-event-empty">
                          {selected.status === 'running' ? 'Waiting for events…' : 'Start the pipeline to see events'}
                        </div>
                      ) : sseEvents.map((ev, i) => (
                        <div key={i} className={`streaming-event-row streaming-event-row--${ev.type}`}>
                          <span className="streaming-event-type">{ev.type}</span>
                          <span className="streaming-event-detail">
                            {ev.type === 'window_closed'
                              ? `key=${ev.window_key}  events=${ev.event_count}  agg=${JSON.stringify(ev.aggregations)}`
                              : ev.type === 'late_event'
                                ? `key=${ev.key}  ts=${ev.timestamp}`
                                : JSON.stringify(ev).slice(0, 120)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ) : pipelines.length > 0 ? (
                <div className="streaming-detail streaming-detail--empty">
                  <p>Select a pipeline to view live metrics and events</p>
                </div>
              ) : null}
            </div>
          </>
        );
      })()}
    </div>
  );
};

export default StreamingPanel;
