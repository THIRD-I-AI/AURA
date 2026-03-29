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

const STATUS_COLORS: Record<string, string> = {
  running: '#22c55e',
  paused: '#f59e0b',
  stopped: '#6b7280',
  failed: '#ef4444',
  draft: '#8b5cf6',
  starting: '#3b82f6',
  stopping: '#f59e0b',
};

const STATUS_LABELS: Record<string, string> = {
  running: 'Running',
  paused: 'Paused',
  stopped: 'Stopped',
  failed: 'Failed',
  draft: 'Draft',
  starting: 'Starting\u2026',
  stopping: 'Stopping\u2026',
};

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function formatEpoch(epoch: number): string {
  if (!epoch) return '\u2014';
  return new Date(epoch * 1000).toLocaleTimeString();
}

/** Build a default config dict from schema fields */
function defaultsFromFields(fields: SchemaField[]): Record<string, any> {
  const out: Record<string, any> = {};
  for (const f of fields) {
    if (f.default !== undefined) out[f.key] = f.default;
  }
  return out;
}

/* ================================================================
   Dynamic Schema Field Renderer
   ================================================================ */

interface FieldRendererProps {
  field: SchemaField;
  value: any;
  onChange: (key: string, value: any) => void;
}

const FieldRenderer: React.FC<FieldRendererProps> = ({ field, value, onChange }) => {
  if (field.type === 'select') {
    return (
      <div className="sf-field">
        <label className="sf-label">
          {field.label}
          {field.required && <span className="sf-required">*</span>}
        </label>
        <select
          className="sf-input sf-select"
          value={value ?? field.default ?? ''}
          onChange={(e) => onChange(field.key, e.target.value)}
        >
          {(field.options || []).map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
        {field.help && <span className="sf-help">{field.help}</span>}
      </div>
    );
  }

  if (field.type === 'number') {
    return (
      <div className="sf-field">
        <label className="sf-label">
          {field.label}
          {field.required && <span className="sf-required">*</span>}
        </label>
        <input
          className="sf-input"
          type="number"
          step="any"
          value={value ?? field.default ?? ''}
          onChange={(e) => onChange(field.key, e.target.value === '' ? '' : Number(e.target.value))}
        />
        {field.help && <span className="sf-help">{field.help}</span>}
      </div>
    );
  }

  // Default: text
  return (
    <div className="sf-field">
      <label className="sf-label">
        {field.label}
        {field.required && <span className="sf-required">*</span>}
      </label>
      <input
        className="sf-input"
        type="text"
        value={value ?? field.default ?? ''}
        onChange={(e) => onChange(field.key, e.target.value)}
      />
      {field.help && <span className="sf-help">{field.help}</span>}
    </div>
  );
};

/* ================================================================
   Alert Rule Builder
   ================================================================ */

interface AlertRule {
  field: string;
  operator: string;
  threshold: number;
  label: string;
}

const AlertRuleBuilder: React.FC<{
  rules: AlertRule[];
  onChange: (rules: AlertRule[]) => void;
}> = ({ rules, onChange }) => {
  const addRule = () => {
    onChange([...rules, { field: '', operator: '>', threshold: 0, label: '' }]);
  };
  const removeRule = (idx: number) => {
    onChange(rules.filter((_, i) => i !== idx));
  };
  const updateRule = (idx: number, key: keyof AlertRule, val: any) => {
    const updated = rules.map((r, i) => i === idx ? { ...r, [key]: val } : r);
    onChange(updated);
  };

  return (
    <div className="sf-alert-rules">
      <label className="sf-label">Alert Rules <span className="sf-required">*</span></label>
      {rules.map((rule, idx) => (
        <div key={idx} className="sf-alert-rule-row">
          <input className="sf-input sf-input--sm" placeholder="Field" value={rule.field} onChange={(e) => updateRule(idx, 'field', e.target.value)} />
          <select className="sf-input sf-select sf-input--xs" value={rule.operator} onChange={(e) => updateRule(idx, 'operator', e.target.value)}>
            {['>', '>=', '<', '<=', '=='].map((op) => <option key={op} value={op}>{op}</option>)}
          </select>
          <input className="sf-input sf-input--sm" type="number" step="any" placeholder="Threshold" value={rule.threshold} onChange={(e) => updateRule(idx, 'threshold', Number(e.target.value))} />
          <input className="sf-input sf-input--sm" placeholder="Alert label" value={rule.label} onChange={(e) => updateRule(idx, 'label', e.target.value)} />
          <button className="streaming-btn streaming-btn--ghost sf-btn-rm" onClick={() => removeRule(idx)} type="button">&times;</button>
        </div>
      ))}
      <button className="streaming-btn streaming-btn--primary sf-btn-add" onClick={addRule} type="button">+ Add Rule</button>
    </div>
  );
};

/* ================================================================
   Aggregation Field Builder
   ================================================================ */

interface AggField {
  field: string;
  function: string;
}

const AggFieldBuilder: React.FC<{
  fields: AggField[];
  onChange: (fields: AggField[]) => void;
}> = ({ fields, onChange }) => {
  const add = () => {
    onChange([...fields, { field: '', function: 'COUNT' }]);
  };
  const remove = (idx: number) => {
    onChange(fields.filter((_, i) => i !== idx));
  };
  const update = (idx: number, key: keyof AggField, val: string) => {
    const updated = fields.map((f, i) => i === idx ? { ...f, [key]: val } : f);
    onChange(updated);
  };

  return (
    <div className="sf-agg-fields">
      <label className="sf-label">Aggregations <span className="sf-required">*</span></label>
      {fields.map((agg, idx) => (
        <div key={idx} className="sf-agg-row">
          <input className="sf-input sf-input--sm" placeholder="Field name" value={agg.field} onChange={(e) => update(idx, 'field', e.target.value)} />
          <select className="sf-input sf-select sf-input--xs" value={agg.function} onChange={(e) => update(idx, 'function', e.target.value)}>
            {['SUM', 'COUNT', 'MIN', 'MAX', 'AVG'].map((fn) => <option key={fn} value={fn}>{fn}</option>)}
          </select>
          <button className="streaming-btn streaming-btn--ghost sf-btn-rm" onClick={() => remove(idx)} type="button">&times;</button>
        </div>
      ))}
      <button className="streaming-btn streaming-btn--primary sf-btn-add" onClick={add} type="button">+ Add Aggregation</button>
    </div>
  );
};

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

  // ── Fetch data ──
  const fetchPipelines = useCallback(async () => {
    try {
      const data = await streamingService.list();
      setPipelines(data.pipelines || []);
    } catch { /* Backend may be down */ }
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
      // Set default source type from first implemented source
      if (data.sources) {
        const firstImpl = Object.entries(data.sources).find(([, s]) => s.implemented);
        if (firstImpl) {
          setFormSourceType(firstImpl[0]);
          setFormSourceConfig(defaultsFromFields(firstImpl[1].fields));
        }
      }
      // Set default window config
      if (data.windows?.tumbling) {
        setFormWindowConfig(defaultsFromFields(data.windows.tumbling.fields));
      }
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
    const url = streamingService.streamUrl(pipelineId);
    const es = new EventSource(url);
    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'metrics') {
          setLiveMetrics(data as StreamPipelineMetrics);
        } else {
          setSseEvents((prev) => [data, ...prev].slice(0, 100));
        }
      } catch { /* ignore */ }
    };
    es.onerror = () => { /* auto-reconnect */ };
    sseRef.current = es;
  }, []);

  const disconnectSSE = useCallback(() => {
    if (sseRef.current) { sseRef.current.close(); sseRef.current = null; }
  }, []);

  // ── Polling fallback ──
  useEffect(() => {
    if (selectedPipeline) {
      const poll = async () => {
        try { const m = await streamingService.metrics(selectedPipeline); setLiveMetrics(m); } catch { /* */ }
      };
      poll();
      metricsTimerRef.current = window.setInterval(poll, 2000);
    }
    return () => { if (metricsTimerRef.current) clearInterval(metricsTimerRef.current); };
  }, [selectedPipeline]);

  useEffect(() => () => disconnectSSE(), [disconnectSSE]);

  // ── Lifecycle ──
  const handleStart = async (id: string) => {
    try { setIsLoading(true); await streamingService.start(id); showToast('Pipeline started'); connectSSE(id); await fetchPipelines(); }
    catch (err: any) { setError(err?.message || 'Failed to start'); }
    finally { setIsLoading(false); }
  };
  const handleStop = async (id: string) => {
    try { setIsLoading(true); await streamingService.stop(id); showToast('Pipeline stopped'); disconnectSSE(); await fetchPipelines(); }
    catch (err: any) { setError(err?.message || 'Failed to stop'); }
    finally { setIsLoading(false); }
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

  const handleCreateFromTemplate = async (tmpl: StreamTemplate) => {
    try {
      setIsLoading(true);
      const created = await streamingService.create(tmpl.pipeline as any);
      showToast(`Pipeline "${created.name}" created`);
      setActiveTab('pipelines');
      await fetchPipelines();
    } catch (err: any) { setError(err?.message || 'Failed to create'); }
    finally { setIsLoading(false); }
  };

  const handleSelect = (id: string) => {
    setSelectedPipeline(id);
    setSseEvents([]);
    setLiveMetrics(null);
    const pipe = pipelines.find((p) => p.id === id);
    if (pipe && pipe.status === 'running') connectSSE(id); else disconnectSSE();
  };

  // ── Form: source type change ──
  const handleSourceTypeChange = (newType: string) => {
    setFormSourceType(newType);
    if (schemas?.sources[newType]) {
      setFormSourceConfig(defaultsFromFields(schemas.sources[newType].fields));
    } else {
      setFormSourceConfig({});
    }
  };

  // ── Form: window type change ──
  const handleWindowTypeChange = (newType: string) => {
    setFormWindowType(newType);
    if (schemas?.windows[newType]) {
      setFormWindowConfig(defaultsFromFields(schemas.windows[newType].fields));
    } else {
      setFormWindowConfig({});
    }
  };

  // ── Form: add/remove sink ──
  const handleAddSink = (sinkType: string) => {
    if (formSinks.some((s) => s.type === sinkType)) return;
    const sinkSchema = schemas?.sinks[sinkType];
    const cfg = sinkSchema ? defaultsFromFields(sinkSchema.fields) : {};
    setFormSinks([...formSinks, { type: sinkType, config: cfg }]);
  };

  const handleRemoveSink = (idx: number) => {
    setFormSinks(formSinks.filter((_, i) => i !== idx));
  };

  const handleSinkConfigChange = (idx: number, key: string, value: any) => {
    setFormSinks(formSinks.map((s, i) => i === idx ? { ...s, config: { ...s.config, [key]: value } } : s));
  };

  // ── Form: add/remove transform ──
  const handleAddTransform = (tType: string) => {
    const tSchema = schemas?.transforms[tType];
    const cfg: Record<string, any> = {};
    if (tSchema) {
      for (const f of tSchema.fields) {
        if (f.type === 'agg_fields') cfg[f.key] = [];
        else if (f.default !== undefined) cfg[f.key] = f.default;
        else cfg[f.key] = '';
      }
    }
    setFormTransforms([...formTransforms, { type: tType, description: tSchema?.label || tType, config: cfg }]);
  };

  const handleRemoveTransform = (idx: number) => {
    setFormTransforms(formTransforms.filter((_, i) => i !== idx));
  };

  const handleTransformConfigChange = (idx: number, key: string, value: any) => {
    setFormTransforms(formTransforms.map((t, i) => i === idx ? { ...t, config: { ...t.config, [key]: value } } : t));
  };

  // ── Form: submit ──
  const handleCreatePipeline = async () => {
    if (!formName.trim()) { setError('Pipeline name is required'); return; }
    if (!formSourceType) { setError('Select a source type'); return; }

    // Build window config from form
    const windowPayload: any = { type: formWindowType, ...formWindowConfig };

    // Build sinks — ensure SSE is always included
    const sinkPayload = [...formSinks];
    if (!sinkPayload.some((s) => s.type === 'sse')) {
      sinkPayload.push({ type: 'sse', config: {} });
    }

    const payload = {
      name: formName.trim(),
      description: formDesc.trim(),
      source: { type: formSourceType, config: formSourceConfig },
      event_time_field: formEventTimeField,
      watermark_delay_seconds: formWatermarkDelay,
      window: windowPayload,
      transforms: formTransforms,
      sinks: sinkPayload,
      checkpoint_interval_seconds: formCheckpointInterval,
    };

    try {
      setIsLoading(true);
      const created = await streamingService.create(payload);
      showToast(`Pipeline "${created.name}" created`);
      // Reset form
      setFormName(''); setFormDesc('');
      setFormSinks([]); setFormTransforms([]);
      setActiveTab('pipelines');
      await fetchPipelines();
    } catch (err: any) {
      setError(err?.message || 'Failed to create pipeline');
    } finally {
      setIsLoading(false);
    }
  };

  // ── Render ──
  const selected = pipelines.find((p) => p.id === selectedPipeline);
  const currentSourceSchema = schemas?.sources[formSourceType];
  const currentWindowSchema = schemas?.windows[formWindowType];

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
          { key: 'pipelines' as TabType, label: '\uD83C\uDF0A My Pipelines' },
          { key: 'create' as TabType, label: '+ Create Pipeline' },
          { key: 'templates' as TabType, label: '\uD83D\uDCCB Templates' },
        ]).map(({ key, label }) => (
          <button
            key={key}
            className={`streaming-tab ${activeTab === key ? 'streaming-tab--active' : ''}`}
            onClick={() => setActiveTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ════════════════ CREATE TAB ════════════════ */}
      {activeTab === 'create' && schemas && (
        <div className="streaming-create-form">
          <h2>Create Streaming Pipeline</h2>
          <p className="sf-subtitle">Configure a real-time data pipeline with your own sources, sinks, and processing logic.</p>

          {/* ── Basics ── */}
          <fieldset className="sf-section">
            <legend>Pipeline Info</legend>
            <div className="sf-row">
              <div className="sf-field sf-field--wide">
                <label className="sf-label">Pipeline Name <span className="sf-required">*</span></label>
                <input className="sf-input" type="text" placeholder="e.g. Sales Order Monitor" value={formName} onChange={(e) => setFormName(e.target.value)} />
              </div>
              <div className="sf-field sf-field--wide">
                <label className="sf-label">Description</label>
                <input className="sf-input" type="text" placeholder="What does this pipeline do?" value={formDesc} onChange={(e) => setFormDesc(e.target.value)} />
              </div>
            </div>
          </fieldset>

          {/* ── Source ── */}
          <fieldset className="sf-section">
            <legend>Data Source</legend>
            <div className="sf-field">
              <label className="sf-label">Source Type <span className="sf-required">*</span></label>
              <div className="sf-source-cards">
                {Object.entries(schemas.sources).map(([key, src]) => (
                  <button
                    key={key}
                    type="button"
                    className={`sf-source-card ${formSourceType === key ? 'sf-source-card--selected' : ''} ${!src.implemented ? 'sf-source-card--disabled' : ''}`}
                    onClick={() => src.implemented && handleSourceTypeChange(key)}
                    disabled={!src.implemented}
                  >
                    <strong>{src.label}</strong>
                    <span>{src.description}</span>
                    {!src.implemented && <span className="sf-coming-soon">Coming Soon</span>}
                  </button>
                ))}
              </div>
            </div>
            {currentSourceSchema && currentSourceSchema.fields.length > 0 && (
              <div className="sf-row">
                {currentSourceSchema.fields.map((field) => (
                  <FieldRenderer
                    key={field.key}
                    field={field}
                    value={formSourceConfig[field.key]}
                    onChange={(k, v) => setFormSourceConfig({ ...formSourceConfig, [k]: v })}
                  />
                ))}
              </div>
            )}
            <div className="sf-row">
              <div className="sf-field">
                <label className="sf-label">Event Time Field</label>
                <input className="sf-input" type="text" value={formEventTimeField} onChange={(e) => setFormEventTimeField(e.target.value)} />
                <span className="sf-help">Field in event data that holds the timestamp</span>
              </div>
              <div className="sf-field">
                <label className="sf-label">Watermark Delay (s)</label>
                <input className="sf-input" type="number" value={formWatermarkDelay} onChange={(e) => setFormWatermarkDelay(Number(e.target.value))} />
                <span className="sf-help">How long to wait for late events before advancing the watermark</span>
              </div>
            </div>
          </fieldset>

          {/* ── Window ── */}
          <fieldset className="sf-section">
            <legend>Window Strategy</legend>
            <div className="sf-field">
              <label className="sf-label">Window Type</label>
              <div className="sf-window-cards">
                {Object.entries(schemas.windows).map(([key, win]) => (
                  <button
                    key={key}
                    type="button"
                    className={`sf-window-card ${formWindowType === key ? 'sf-window-card--selected' : ''}`}
                    onClick={() => handleWindowTypeChange(key)}
                  >
                    <strong>{win.label}</strong>
                    <span>{win.description}</span>
                  </button>
                ))}
              </div>
            </div>
            {currentWindowSchema && currentWindowSchema.fields.length > 0 && (
              <div className="sf-row">
                {currentWindowSchema.fields.map((field) => (
                  <FieldRenderer
                    key={field.key}
                    field={field}
                    value={formWindowConfig[field.key]}
                    onChange={(k, v) => setFormWindowConfig({ ...formWindowConfig, [k]: v })}
                  />
                ))}
              </div>
            )}
          </fieldset>

          {/* ── Transforms ── */}
          <fieldset className="sf-section">
            <legend>Transforms (optional)</legend>
            <p className="sf-help-block">Add processing steps that run on each event before windowing.</p>
            {formTransforms.map((t, idx) => {
              const tSchema = schemas.transforms[t.type];
              return (
                <div key={idx} className="sf-transform-block">
                  <div className="sf-transform-header">
                    <strong>{tSchema?.label || t.type}</strong>
                    <button className="streaming-btn streaming-btn--ghost sf-btn-rm" onClick={() => handleRemoveTransform(idx)} type="button">&times;</button>
                  </div>
                  <div className="sf-row">
                    {tSchema?.fields.map((field) => {
                      if (field.type === 'agg_fields') {
                        return (
                          <AggFieldBuilder
                            key={field.key}
                            fields={t.config[field.key] || []}
                            onChange={(aggs) => handleTransformConfigChange(idx, field.key, aggs)}
                          />
                        );
                      }
                      return (
                        <FieldRenderer
                          key={field.key}
                          field={field}
                          value={t.config[field.key]}
                          onChange={(k, v) => handleTransformConfigChange(idx, k, v)}
                        />
                      );
                    })}
                  </div>
                </div>
              );
            })}
            <div className="sf-add-btns">
              {Object.entries(schemas.transforms).map(([key, ts]) => (
                <button
                  key={key}
                  type="button"
                  className="streaming-btn streaming-btn--primary sf-btn-add"
                  onClick={() => handleAddTransform(key)}
                >
                  + {ts.label}
                </button>
              ))}
            </div>
          </fieldset>

          {/* ── Sinks ── */}
          <fieldset className="sf-section">
            <legend>Output Sinks</legend>
            <p className="sf-help-block">Choose where processed window results are sent. SSE (live to UI) is always included.</p>
            {formSinks.map((s, idx) => {
              const sinkSchema = schemas.sinks[s.type];
              return (
                <div key={idx} className="sf-sink-block">
                  <div className="sf-sink-header">
                    <strong>{sinkSchema?.label || s.type}</strong>
                    <button className="streaming-btn streaming-btn--ghost sf-btn-rm" onClick={() => handleRemoveSink(idx)} type="button">&times;</button>
                  </div>
                  <div className="sf-row">
                    {sinkSchema?.fields.map((field) => {
                      if (field.type === 'alert_rules') {
                        return (
                          <AlertRuleBuilder
                            key={field.key}
                            rules={s.config[field.key] || []}
                            onChange={(rules) => handleSinkConfigChange(idx, field.key, rules)}
                          />
                        );
                      }
                      return (
                        <FieldRenderer
                          key={field.key}
                          field={field}
                          value={s.config[field.key]}
                          onChange={(k, v) => handleSinkConfigChange(idx, k, v)}
                        />
                      );
                    })}
                  </div>
                </div>
              );
            })}
            <div className="sf-add-btns">
              {Object.entries(schemas.sinks).filter(([, s]) => s.implemented).map(([key, sink]) => (
                <button
                  key={key}
                  type="button"
                  className={`streaming-btn sf-btn-add ${formSinks.some((s) => s.type === key) ? 'streaming-btn--ghost' : 'streaming-btn--primary'}`}
                  onClick={() => handleAddSink(key)}
                  disabled={formSinks.some((s) => s.type === key)}
                >
                  {formSinks.some((s) => s.type === key) ? `\u2713 ${sink.label}` : `+ ${sink.label}`}
                </button>
              ))}
            </div>
          </fieldset>

          {/* ── Advanced ── */}
          <fieldset className="sf-section">
            <legend>Advanced Settings</legend>
            <div className="sf-row">
              <div className="sf-field">
                <label className="sf-label">Checkpoint Interval (s)</label>
                <input className="sf-input" type="number" value={formCheckpointInterval} onChange={(e) => setFormCheckpointInterval(Number(e.target.value))} />
                <span className="sf-help">How often to save pipeline state for recovery</span>
              </div>
            </div>
          </fieldset>

          {/* ── Submit ── */}
          <div className="sf-submit-row">
            <button className="streaming-btn streaming-btn--primary sf-submit-btn" onClick={handleCreatePipeline} disabled={isLoading || !formName.trim()}>
              {isLoading ? 'Creating\u2026' : 'Create Pipeline'}
            </button>
            <button className="streaming-btn streaming-btn--ghost" onClick={() => setActiveTab('pipelines')} type="button">Cancel</button>
          </div>
        </div>
      )}

      {activeTab === 'create' && !schemas && (
        <div className="streaming-empty">Loading pipeline configuration schemas\u2026 Is the backend running?</div>
      )}

      {/* ════════════════ TEMPLATES TAB ════════════════ */}
      {activeTab === 'templates' && (
        <div className="streaming-templates">
          <p className="sf-help-block" style={{ marginBottom: '1rem' }}>
            Templates are pre-configured starting points. Create one, then customize it to fit your needs.
          </p>
          {templates.length === 0 && (
            <div className="streaming-empty">No templates available \u2014 is the backend running?</div>
          )}
          <div className="streaming-template-grid">
            {templates.map((tmpl) => (
              <div key={tmpl.id} className="streaming-template-card">
                <div className="streaming-template-header">
                  <h3>{tmpl.name}</h3>
                  <div className="streaming-template-tags">
                    {tmpl.tags.map((t) => (
                      <span key={t} className="streaming-tag">{t}</span>
                    ))}
                  </div>
                </div>
                <p className="streaming-template-desc">{tmpl.description}</p>
                <div className="streaming-template-meta">
                  <span>Source: {tmpl.pipeline.source?.type}</span>
                  <span>Window: {tmpl.pipeline.window?.type} ({tmpl.pipeline.window?.size_seconds || tmpl.pipeline.window?.gap_seconds}s)</span>
                </div>
                <button className="streaming-btn streaming-btn--primary" onClick={() => handleCreateFromTemplate(tmpl)} disabled={isLoading}>
                  + Create Pipeline
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ════════════════ PIPELINES TAB ════════════════ */}
      {activeTab === 'pipelines' && (
        <div className="streaming-content">
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
              <div
                key={pipe.id}
                className={`streaming-pipeline-card ${selectedPipeline === pipe.id ? 'streaming-pipeline-card--selected' : ''}`}
                onClick={() => handleSelect(pipe.id)}
              >
                <div className="streaming-pipeline-header">
                  <h3>{pipe.name}</h3>
                  <span className="streaming-status-badge" style={{ background: STATUS_COLORS[pipe.status] || '#6b7280' }}>
                    {STATUS_LABELS[pipe.status] || pipe.status}
                  </span>
                </div>
                <p className="streaming-pipeline-desc">{pipe.description || '\u2014'}</p>
                <div className="streaming-pipeline-meta">
                  <span>\uD83D\uDCE5 {pipe.source.type}</span>
                  <span>\uD83E\uDE9F {pipe.window.type} ({pipe.window.size_seconds || pipe.window.gap_seconds}s)</span>
                  <span>\uD83D\uDCE4 {pipe.sinks.map((s) => s.type).join(', ')}</span>
                </div>
                <div className="streaming-pipeline-actions">
                  {(pipe.status === 'draft' || pipe.status === 'stopped' || pipe.status === 'failed') && (
                    <button className="streaming-btn streaming-btn--success" onClick={(e) => { e.stopPropagation(); handleStart(pipe.id); }} disabled={isLoading}>
                      \u25B6 Start
                    </button>
                  )}
                  {pipe.status === 'running' && (
                    <>
                      <button className="streaming-btn streaming-btn--warning" onClick={(e) => { e.stopPropagation(); handlePause(pipe.id); }}>
                        \u23F8 Pause
                      </button>
                      <button className="streaming-btn streaming-btn--danger" onClick={(e) => { e.stopPropagation(); handleStop(pipe.id); }}>
                        \u23F9 Stop
                      </button>
                    </>
                  )}
                  {pipe.status === 'paused' && (
                    <button className="streaming-btn streaming-btn--success" onClick={(e) => { e.stopPropagation(); handleResume(pipe.id); }}>
                      \u25B6 Resume
                    </button>
                  )}
                  {pipe.status !== 'running' && pipe.status !== 'starting' && (
                    <button className="streaming-btn streaming-btn--ghost" onClick={(e) => { e.stopPropagation(); handleDelete(pipe.id); }}>
                      \uD83D\uDDD1
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Detail panel */}
          {selected && (
            <div className="streaming-detail">
              <h2>{selected.name}</h2>

              <div className="streaming-kpi-grid">
                <div className="streaming-kpi"><span className="streaming-kpi-label">Events In</span><span className="streaming-kpi-value">{liveMetrics?.events_in?.toLocaleString() ?? '\u2014'}</span></div>
                <div className="streaming-kpi"><span className="streaming-kpi-label">Events Out</span><span className="streaming-kpi-value">{liveMetrics?.events_out?.toLocaleString() ?? '\u2014'}</span></div>
                <div className="streaming-kpi"><span className="streaming-kpi-label">Events/sec</span><span className="streaming-kpi-value">{liveMetrics?.events_per_second ?? '\u2014'}</span></div>
                <div className="streaming-kpi"><span className="streaming-kpi-label">Late Events</span><span className="streaming-kpi-value streaming-kpi-value--warn">{liveMetrics?.events_late ?? '\u2014'}</span></div>
                <div className="streaming-kpi"><span className="streaming-kpi-label">Active Windows</span><span className="streaming-kpi-value">{liveMetrics?.active_windows ?? '\u2014'}</span></div>
                <div className="streaming-kpi"><span className="streaming-kpi-label">Closed Windows</span><span className="streaming-kpi-value">{liveMetrics?.closed_windows ?? '\u2014'}</span></div>
                <div className="streaming-kpi"><span className="streaming-kpi-label">Watermark</span><span className="streaming-kpi-value">{formatEpoch(liveMetrics?.watermark_position || 0)}</span></div>
                <div className="streaming-kpi"><span className="streaming-kpi-label">Uptime</span><span className="streaming-kpi-value">{formatUptime(liveMetrics?.uptime_seconds || 0)}</span></div>
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
                  <div><strong>Source</strong><br />{selected.source.type} \u2014 {JSON.stringify(selected.source.config)}</div>
                  <div><strong>Window</strong><br />{selected.window.type} ({selected.window.size_seconds || selected.window.gap_seconds}s) \u2014 late: {selected.window.late_data_policy}</div>
                  <div><strong>Transforms</strong><br />{selected.transforms.length > 0 ? selected.transforms.map((t) => `${t.type}: ${t.description}`).join(' \u2192 ') : 'None'}</div>
                  <div><strong>Sinks</strong><br />{selected.sinks.map((s) => s.type).join(', ')}</div>
                  <div><strong>Checkpoint</strong><br />Every {selected.checkpoint_interval_seconds}s</div>
                  <div><strong>Watermark Delay</strong><br />{selected.watermark_delay_seconds}s</div>
                </div>
              </div>

              <div className="streaming-event-feed">
                <h4>Live Events ({sseEvents.length})</h4>
                <div className="streaming-event-list">
                  {sseEvents.length === 0 && (
                    <div className="streaming-event-empty">
                      {selected.status === 'running' ? 'Waiting for events\u2026' : 'Start the pipeline to see events'}
                    </div>
                  )}
                  {sseEvents.map((ev, i) => (
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
          )}

          {!selected && pipelines.length > 0 && (
            <div className="streaming-detail streaming-detail--empty">
              <p>Select a pipeline to view live metrics and events</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default StreamingPanel;
