import React, { useState, useEffect, useRef, useCallback } from 'react';
import { type PageType } from '../components/Layout/AppLayout';
import {
  streamingService,
  type StreamPipelineDef,
  type StreamPipelineMetrics,
  type StreamTemplate,
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

/* ================================================================
   Main Component
   ================================================================ */

const StreamingPanel: React.FC<StreamingPanelProps> = () => {
  const [activeTab, setActiveTab] = useState<TabType>('pipelines');
  const [pipelines, setPipelines] = useState<StreamPipelineDef[]>([]);
  const [templates, setTemplates] = useState<StreamTemplate[]>([]);
  const [selectedPipeline, setSelectedPipeline] = useState<string | null>(null);
  const [liveMetrics, setLiveMetrics] = useState<StreamPipelineMetrics | null>(null);
  const [sseEvents, setSseEvents] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const sseRef = useRef<EventSource | null>(null);
  const metricsTimerRef = useRef<number | null>(null);

  // ── Fetch pipelines ──
  const fetchPipelines = useCallback(async () => {
    try {
      const data = await streamingService.list();
      setPipelines(data.pipelines || []);
    } catch {
      // Backend may be down
    }
  }, []);

  const fetchTemplates = useCallback(async () => {
    try {
      const data = await streamingService.templates();
      setTemplates(data.templates || []);
    } catch {
      // Backend may not have templates endpoint
    }
  }, []);

  useEffect(() => {
    fetchPipelines();
    fetchTemplates();
  }, [fetchPipelines, fetchTemplates]);

  // ── Toast helper ──
  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  // ── SSE connection ──
  const connectSSE = useCallback((pipelineId: string) => {
    if (sseRef.current) {
      sseRef.current.close();
    }
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
      } catch { /* ignore parse errors */ }
    };
    es.onerror = () => {
      // will auto-reconnect
    };
    sseRef.current = es;
  }, []);

  const disconnectSSE = useCallback(() => {
    if (sseRef.current) {
      sseRef.current.close();
      sseRef.current = null;
    }
  }, []);

  // ── Polling fallback for metrics ──
  useEffect(() => {
    if (selectedPipeline) {
      const poll = async () => {
        try {
          const m = await streamingService.metrics(selectedPipeline);
          setLiveMetrics(m);
        } catch { /* ignore */ }
      };
      poll();
      metricsTimerRef.current = window.setInterval(poll, 2000);
    }
    return () => {
      if (metricsTimerRef.current) clearInterval(metricsTimerRef.current);
    };
  }, [selectedPipeline]);

  // ── Cleanup SSE on unmount ──
  useEffect(() => () => disconnectSSE(), [disconnectSSE]);

  // ── Lifecycle actions ──
  const handleStart = async (id: string) => {
    try {
      setIsLoading(true);
      await streamingService.start(id);
      showToast('Pipeline started');
      connectSSE(id);
      await fetchPipelines();
    } catch (err: any) {
      setError(err?.message || 'Failed to start');
    } finally {
      setIsLoading(false);
    }
  };

  const handleStop = async (id: string) => {
    try {
      setIsLoading(true);
      await streamingService.stop(id);
      showToast('Pipeline stopped');
      disconnectSSE();
      await fetchPipelines();
    } catch (err: any) {
      setError(err?.message || 'Failed to stop');
    } finally {
      setIsLoading(false);
    }
  };

  const handlePause = async (id: string) => {
    try {
      await streamingService.pause(id);
      showToast('Pipeline paused');
      await fetchPipelines();
    } catch (err: any) {
      setError(err?.message || 'Failed to pause');
    }
  };

  const handleResume = async (id: string) => {
    try {
      await streamingService.resume(id);
      showToast('Pipeline resumed');
      await fetchPipelines();
    } catch (err: any) {
      setError(err?.message || 'Failed to resume');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await streamingService.remove(id);
      showToast('Pipeline deleted');
      if (selectedPipeline === id) {
        setSelectedPipeline(null);
        setLiveMetrics(null);
        disconnectSSE();
      }
      await fetchPipelines();
    } catch (err: any) {
      setError(err?.message || 'Failed to delete');
    }
  };

  const handleCreateFromTemplate = async (tmpl: StreamTemplate) => {
    try {
      setIsLoading(true);
      const created = await streamingService.create(tmpl.pipeline as any);
      showToast(`Pipeline "${created.name}" created`);
      setActiveTab('pipelines');
      await fetchPipelines();
    } catch (err: any) {
      setError(err?.message || 'Failed to create');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSelect = (id: string) => {
    setSelectedPipeline(id);
    setSseEvents([]);
    setLiveMetrics(null);
    const pipe = pipelines.find((p) => p.id === id);
    if (pipe && pipe.status === 'running') {
      connectSSE(id);
    } else {
      disconnectSSE();
    }
  };

  // ── Render helpers ──
  const selected = pipelines.find((p) => p.id === selectedPipeline);

  return (
    <div className="streaming-panel">
      {/* Toast */}
      {toast && <div className="streaming-toast">{toast}</div>}
      {error && (
        <div className="streaming-error">
          <span>{error}</span>
          <button onClick={() => setError(null)}>×</button>
        </div>
      )}

      {/* Tabs */}
      <div className="streaming-tabs">
        {(['pipelines', 'templates'] as TabType[]).map((tab) => (
          <button
            key={tab}
            className={`streaming-tab ${activeTab === tab ? 'streaming-tab--active' : ''}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab === 'pipelines' ? '🌊 My Pipelines' : '📋 Templates'}
          </button>
        ))}
      </div>

      {/* Templates tab */}
      {activeTab === 'templates' && (
        <div className="streaming-templates">
          {templates.length === 0 && (
            <div className="streaming-empty">No templates available — is the backend running?</div>
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
                <button
                  className="streaming-btn streaming-btn--primary"
                  onClick={() => handleCreateFromTemplate(tmpl)}
                  disabled={isLoading}
                >
                  + Create Pipeline
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Pipelines tab */}
      {activeTab === 'pipelines' && (
        <div className="streaming-content">
          {/* Pipeline list */}
          <div className="streaming-list">
            {pipelines.length === 0 && (
              <div className="streaming-empty">
                <p>No streaming pipelines yet.</p>
                <button
                  className="streaming-btn streaming-btn--primary"
                  onClick={() => setActiveTab('templates')}
                >
                  Browse Templates
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
                  <span
                    className="streaming-status-badge"
                    style={{ background: STATUS_COLORS[pipe.status] || '#6b7280' }}
                  >
                    {STATUS_LABELS[pipe.status] || pipe.status}
                  </span>
                </div>
                <p className="streaming-pipeline-desc">{pipe.description || '—'}</p>
                <div className="streaming-pipeline-meta">
                  <span>📥 {pipe.source.type}</span>
                  <span>🪟 {pipe.window.type} ({pipe.window.size_seconds || pipe.window.gap_seconds}s)</span>
                  <span>📤 {pipe.sinks.map((s) => s.type).join(', ')}</span>
                </div>
                <div className="streaming-pipeline-actions">
                  {(pipe.status === 'draft' || pipe.status === 'stopped' || pipe.status === 'failed') && (
                    <button className="streaming-btn streaming-btn--success" onClick={(e) => { e.stopPropagation(); handleStart(pipe.id); }} disabled={isLoading}>
                      ▶ Start
                    </button>
                  )}
                  {pipe.status === 'running' && (
                    <>
                      <button className="streaming-btn streaming-btn--warning" onClick={(e) => { e.stopPropagation(); handlePause(pipe.id); }}>
                        ⏸ Pause
                      </button>
                      <button className="streaming-btn streaming-btn--danger" onClick={(e) => { e.stopPropagation(); handleStop(pipe.id); }}>
                        ⏹ Stop
                      </button>
                    </>
                  )}
                  {pipe.status === 'paused' && (
                    <button className="streaming-btn streaming-btn--success" onClick={(e) => { e.stopPropagation(); handleResume(pipe.id); }}>
                      ▶ Resume
                    </button>
                  )}
                  {pipe.status !== 'running' && pipe.status !== 'starting' && (
                    <button className="streaming-btn streaming-btn--ghost" onClick={(e) => { e.stopPropagation(); handleDelete(pipe.id); }}>
                      🗑
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Detail / Metrics panel */}
          {selected && (
            <div className="streaming-detail">
              <h2>{selected.name}</h2>

              {/* Live metrics KPIs */}
              <div className="streaming-kpi-grid">
                <div className="streaming-kpi">
                  <span className="streaming-kpi-label">Events In</span>
                  <span className="streaming-kpi-value">{liveMetrics?.events_in?.toLocaleString() ?? '—'}</span>
                </div>
                <div className="streaming-kpi">
                  <span className="streaming-kpi-label">Events Out</span>
                  <span className="streaming-kpi-value">{liveMetrics?.events_out?.toLocaleString() ?? '—'}</span>
                </div>
                <div className="streaming-kpi">
                  <span className="streaming-kpi-label">Events/sec</span>
                  <span className="streaming-kpi-value">{liveMetrics?.events_per_second ?? '—'}</span>
                </div>
                <div className="streaming-kpi">
                  <span className="streaming-kpi-label">Late Events</span>
                  <span className="streaming-kpi-value streaming-kpi-value--warn">{liveMetrics?.events_late ?? '—'}</span>
                </div>
                <div className="streaming-kpi">
                  <span className="streaming-kpi-label">Active Windows</span>
                  <span className="streaming-kpi-value">{liveMetrics?.active_windows ?? '—'}</span>
                </div>
                <div className="streaming-kpi">
                  <span className="streaming-kpi-label">Closed Windows</span>
                  <span className="streaming-kpi-value">{liveMetrics?.closed_windows ?? '—'}</span>
                </div>
                <div className="streaming-kpi">
                  <span className="streaming-kpi-label">Watermark</span>
                  <span className="streaming-kpi-value">{formatEpoch(liveMetrics?.watermark_position || 0)}</span>
                </div>
                <div className="streaming-kpi">
                  <span className="streaming-kpi-label">Uptime</span>
                  <span className="streaming-kpi-value">{formatUptime(liveMetrics?.uptime_seconds || 0)}</span>
                </div>
              </div>

              {/* Errors */}
              {liveMetrics?.errors && liveMetrics.errors.length > 0 && (
                <div className="streaming-errors-box">
                  <h4>Errors</h4>
                  {liveMetrics.errors.map((e, i) => (
                    <div key={i} className="streaming-error-line">{e}</div>
                  ))}
                </div>
              )}

              {/* Pipeline config summary */}
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

              {/* Live event feed */}
              <div className="streaming-event-feed">
                <h4>Live Events ({sseEvents.length})</h4>
                <div className="streaming-event-list">
                  {sseEvents.length === 0 && (
                    <div className="streaming-event-empty">
                      {selected.status === 'running' ? 'Waiting for events…' : 'Start the pipeline to see events'}
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
