import React, { useState, useEffect, useCallback } from 'react';
import { type PageType } from '../components/Layout/AppLayout';
import {
  etlService,
  pipelineService,
  API_BASE_URL,
  type ETLColumnSchema,
  type ETLTransformStep,
  type ETLSourcePreview,
  type ETLExecutionResult,
  type PipelineDef,
  type PipelineRunResult,
  type PipelineListItem,
} from '../services/api';
import './PipelinesPanel.css';
import PipelineMonitor, { type PipelineRunSummary } from '../components/PipelineMonitor';

/* ================================================================
   Types
   ================================================================ */

type TransformType = ETLTransformStep['type'];

const TRANSFORM_TYPES: { value: TransformType; label: string; icon: string }[] = [
  { value: 'filter',       label: 'Filter Rows',     icon: 'FI' },
  { value: 'sort',         label: 'Sort',             icon: 'SO' },
  { value: 'drop_columns', label: 'Drop Columns',     icon: 'DR' },
  { value: 'rename',       label: 'Rename Columns',   icon: 'RN' },
  { value: 'add_column',   label: 'Add Column',       icon: '+C' },
  { value: 'aggregate',    label: 'Aggregate',        icon: 'AG' },
  { value: 'deduplicate',  label: 'Deduplicate',      icon: 'DD' },
  { value: 'cast_type',    label: 'Cast Type',        icon: 'CT' },
  { value: 'fill_missing', label: 'Fill Missing',     icon: 'FM' },
  { value: 'custom_sql',   label: 'Custom SQL',       icon: 'SQL' },
];

const DEST_FORMATS = [
  { value: 'csv',     label: 'CSV',     icon: 'CSV' },
  { value: 'parquet', label: 'Parquet', icon: 'PQ'  },
  { value: 'json',    label: 'JSON',    icon: '{ }' },
];

/* ================================================================
   Pipeline Templates
   ================================================================ */

interface PipelineTemplate {
  name: string;
  description: string;
  icon: string;
  prompt: string;
  tags: string[];
}

const PIPELINE_TEMPLATES: PipelineTemplate[] = [
  {
    name: 'Clean & Deduplicate',
    description: 'Remove duplicates and fill missing values for a clean dataset',
    icon: 'CLN',
    prompt: 'Remove all duplicate rows, fill missing values with appropriate defaults, and export as CSV',
    tags: ['cleaning', 'dedup'],
  },
  {
    name: 'Top-N Analysis',
    description: 'Filter and sort to find the top N records by a metric',
    icon: 'TOP',
    prompt: 'Sort by the main numeric column descending, take the top 100 rows, and export as CSV',
    tags: ['analysis', 'ranking'],
  },
  {
    name: 'Aggregate Summary',
    description: 'Group data by category and compute summary statistics',
    icon: 'AGG',
    prompt: 'Group by the first text/category column, compute COUNT, SUM, and AVG of all numeric columns, and export as CSV',
    tags: ['aggregation', 'summary'],
  },
  {
    name: 'Column Cleanup',
    description: 'Drop unnecessary columns, rename for clarity, and cast types',
    icon: 'SCH',
    prompt: 'Drop any columns that look like IDs or internal fields, rename remaining columns to clean snake_case names, and export as CSV',
    tags: ['schema', 'rename'],
  },
  {
    name: 'Date Filter',
    description: 'Filter records within a specific date range',
    icon: 'DT',
    prompt: 'Filter rows where the date column is within the last 30 days, sort by date descending, and export as CSV',
    tags: ['filter', 'date'],
  },
  {
    name: 'Format Conversion',
    description: 'Convert a file from one format to another with no transforms',
    icon: 'CVT',
    prompt: 'Read the file and export as Parquet format with no transformations',
    tags: ['convert', 'export'],
  },
];

interface PipelinesPanelProps {
  setCurrentPage?: (page: PageType) => void;
}

/* ================================================================
   Main Component
   ================================================================ */

const PipelinesPanel: React.FC<PipelinesPanelProps> = () => {
  // ── State ──
  const [pipelineName, setPipelineName] = useState('');
  const [sourceFiles, setSourceFiles] = useState<string[]>([]);
  const [selectedSource, setSelectedSource] = useState('');
  const [sourcePreview, setSourcePreview] = useState<ETLSourcePreview | null>(null);
  const [transforms, setTransforms] = useState<ETLTransformStep[]>([]);
  const [destFormat, setDestFormat] = useState('csv');
  const [destFilename, setDestFilename] = useState('');
  const [result, setResult] = useState<ETLExecutionResult | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'visual' | 'ai' | 'saved'>('ai');
  const [showAddStep, setShowAddStep] = useState(false);

  // ── AI Pipeline State ──
  const [aiPrompt, setAiPrompt] = useState('');
  const [aiGenerating, setAiGenerating] = useState(false);
  const [aiPipeline, setAiPipeline] = useState<PipelineDef | null>(null);
  const [aiRun, setAiRun] = useState<PipelineRunResult | null>(null);
  const [aiExecuting, setAiExecuting] = useState(false);
  const [aiRunId, setAiRunId] = useState<string | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);

  // ── Kafka source override ──
  const [kafkaEnabled, setKafkaEnabled] = useState(false);
  const [kafkaBootstrap, setKafkaBootstrap] = useState('localhost:9092');
  const [kafkaTopic, setKafkaTopic] = useState('');
  const [kafkaGroupId, setKafkaGroupId] = useState('');
  const [kafkaMaxMessages, setKafkaMaxMessages] = useState(1000);
  const [kafkaTimeoutMs, setKafkaTimeoutMs] = useState(5000);
  const [kafkaFromBeginning, setKafkaFromBeginning] = useState(true);

  // ── Saved Pipelines State ──
  const [savedPipelines, setSavedPipelines] = useState<PipelineListItem[]>([]);
  const [savedLoading, setSavedLoading] = useState(false);
  const [savedError, setSavedError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // ── Toast Notification State ──
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

  const showToast = (message: string, type: 'success' | 'error' = 'success') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  // ── Fetch available uploaded files ──
  useEffect(() => {
    fetchSourceFiles();
  }, []);

  // ── Fetch saved pipelines when tab changes ──
  useEffect(() => {
    if (activeTab === 'saved') {
      fetchSavedPipelines();
    }
  }, [activeTab]);

  const fetchSavedPipelines = async () => {
    setSavedLoading(true);
    setSavedError(null);
    try {
      const resp = await pipelineService.list();
      if (resp.status === 'success') {
        setSavedPipelines(resp.pipelines);
      } else {
        setSavedError('Failed to load saved pipelines');
      }
    } catch (e: any) {
      setSavedError(e.message || 'Failed to load saved pipelines');
    } finally {
      setSavedLoading(false);
    }
  };

  const handleLoadPipeline = async (pipelineId: string) => {
    try {
      const resp = await pipelineService.get(pipelineId);
      if (resp.status === 'success' && resp.pipeline) {
        setAiPipeline(resp.pipeline);
        setAiRun(null);
        setAiError(null);
        setAiPrompt(resp.pipeline.generated_from_prompt || resp.pipeline.description || '');
        setActiveTab('ai');
        showToast(`Loaded pipeline: ${resp.pipeline.name}`);
      }
    } catch (e: any) {
      showToast(e.message || 'Failed to load pipeline', 'error');
    }
  };

  const handleDeletePipeline = async (pipelineId: string, name: string) => {
    if (!confirm(`Delete pipeline "${name}"? This cannot be undone.`)) return;
    setDeletingId(pipelineId);
    try {
      await pipelineService.remove(pipelineId);
      setSavedPipelines(prev => prev.filter(p => p.id !== pipelineId));
      showToast(`Deleted pipeline: ${name}`);
    } catch (e: any) {
      showToast(e.message || 'Failed to delete pipeline', 'error');
    } finally {
      setDeletingId(null);
    }
  };

  const handleUseTemplate = (template: PipelineTemplate) => {
    setAiPrompt(template.prompt);
    setAiPipeline(null);
    setAiRun(null);
    setAiError(null);
    setActiveTab('ai');
    showToast(`Template loaded: ${template.name}`);
  };

  const fetchSourceFiles = async () => {
    try {
      const resp = await fetch(`${API_BASE_URL}/files`);
      const data = await resp.json();
      if (data.status === 'success' && data.files) {
        const DATA_EXTENSIONS = ['.csv', '.json', '.parquet'];
        const names = data.files
          .map((f: any) => f.name || f.filename)
          .filter((n: string) => n && DATA_EXTENSIONS.some(ext => n.toLowerCase().endsWith(ext)));
        setSourceFiles(names);
        console.log('[ETL] Source files loaded:', names);
      }
    } catch (err) {
      console.error('[ETL] Failed to fetch source files:', err);
      setSourceFiles([]);
    }
  };

  // ── Load source preview ──
  const loadSourcePreview = useCallback(async (filename: string) => {
    if (!filename) return;
    setIsLoading(true);
    setError(null);
    try {
      const preview = await etlService.previewSource(filename, 10);
      setSourcePreview(preview);
    } catch (e: any) {
      setError(e.message || 'Failed to preview source file');
      setSourcePreview(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleSourceChange = (file: string) => {
    setSelectedSource(file);
    setResult(null);
    setTransforms([]);
    if (file) loadSourcePreview(file);
  };

  // ── Transform step management ──
  const addTransform = (type: TransformType) => {
    const newStep: ETLTransformStep = {
      id: `step_${Date.now()}`,
      type,
      description: '',
      config: getDefaultConfig(type),
    };
    setTransforms([...transforms, newStep]);
    setShowAddStep(false);
  };

  const updateTransform = (id: string, updates: Partial<ETLTransformStep>) => {
    setTransforms(transforms.map(t => t.id === id ? { ...t, ...updates } : t));
  };

  const removeTransform = (id: string) => {
    setTransforms(transforms.filter(t => t.id !== id));
  };

  const moveTransform = (index: number, direction: 'up' | 'down') => {
    const newIndex = direction === 'up' ? index - 1 : index + 1;
    if (newIndex < 0 || newIndex >= transforms.length) return;
    const updated = [...transforms];
    [updated[index], updated[newIndex]] = [updated[newIndex], updated[index]];
    setTransforms(updated);
  };

  // ── Execute pipeline ──
  const executePipeline = async (previewOnly: boolean) => {
    if (!selectedSource) {
      setError('Please select a source file first.');
      return;
    }
    setIsLoading(true);
    setError(null);
    setResult(null);

    const payload = {
      name: pipelineName || 'Untitled Pipeline',
      source_file: selectedSource,
      destination_format: destFormat,
      destination_filename: destFilename || undefined,
      transforms,
      preview_only: previewOnly,
    };
    console.log('[ETL] Executing pipeline:', JSON.stringify(payload, null, 2));

    try {
      const res = await etlService.execute(payload);
      console.log('[ETL] Pipeline response:', res);
      if (res.status === 'error') {
        const errMsg = res.error || 'Pipeline execution failed';
        console.error('[ETL] Pipeline error:', errMsg);
        setError(errMsg);
      } else {
        setResult(res);
      }
    } catch (e: any) {
      const errMsg = e.message || 'Pipeline execution failed';
      console.error('[ETL] Pipeline exception:', e);
      setError(errMsg);
    } finally {
      setIsLoading(false);
    }
  };

  // ── Download result ──
  const handleDownload = () => {
    if (!result?.output?.file) return;
    const url = etlService.getDownloadUrl(result.output.file);
    window.open(url, '_blank');
  };

  // ── AI Pipeline: Generate ──
  const handleAiGenerate = async () => {
    if (!aiPrompt.trim()) return;
    setAiGenerating(true);
    setAiError(null);
    setAiPipeline(null);
    setAiRun(null);
    console.log('[AI Pipeline] Generating from prompt:', aiPrompt.trim());
    try {
      const resp = await pipelineService.generate(aiPrompt.trim(), selectedSource || undefined);
      console.log('[AI Pipeline] Generate response:', resp);
      if (resp.status === 'success' && resp.pipeline) {
        setAiPipeline(resp.pipeline);
      } else {
        setAiError(resp.error || 'Failed to generate pipeline');
      }
    } catch (e: any) {
      setAiError(e.message || 'Failed to generate pipeline');
    } finally {
      setAiGenerating(false);
    }
  };

  // ── AI Pipeline: Execute (live SSE) ──
  const handleAiExecute = async (previewOnly: boolean) => {
    if (!aiPipeline) return;
    setAiExecuting(true);
    setAiError(null);
    setAiRun(null);
    setAiRunId(null);
    console.log('[AI Pipeline] Executing live (preview=%s):', previewOnly, aiPipeline.name);

    // Apply Kafka source override if enabled
    let pipelineToRun = aiPipeline;
    if (kafkaEnabled) {
      if (!kafkaTopic.trim() || !kafkaBootstrap.trim()) {
        setAiError('Kafka source requires bootstrap_servers and topic');
        setAiExecuting(false);
        return;
      }
      pipelineToRun = {
        ...aiPipeline,
        source: {
          type: 'kafka',
          connection: {
            bootstrap_servers: kafkaBootstrap.trim(),
            topic: kafkaTopic.trim(),
            group_id: kafkaGroupId.trim() || undefined,
            max_messages: kafkaMaxMessages,
            timeout_ms: kafkaTimeoutMs,
            from_beginning: kafkaFromBeginning,
            format: 'json',
          },
        },
      };
    }

    try {
      const resp = await pipelineService.executeAsync(pipelineToRun, previewOnly);
      if (resp.status === 'success' && resp.run_id) {
        setAiRunId(resp.run_id);
      } else {
        setAiError('Pipeline execution could not be started');
        setAiExecuting(false);
      }
    } catch (e: any) {
      setAiError(e.message || 'Pipeline execution failed');
      setAiExecuting(false);
    }
  };

  const handleAiRunComplete = useCallback((summary: PipelineRunSummary) => {
    setAiRun(summary as unknown as PipelineRunResult);
    setAiExecuting(false);
  }, []);

  // ── AI Pipeline: Save ──
  const handleAiSave = async () => {
    if (!aiPipeline) return;
    try {
      const resp = await pipelineService.save(aiPipeline);
      if (resp.status === 'success') {
        setAiError(null);
        showToast(`Pipeline saved: ${resp.name}`);
        // Refresh saved list in background
        fetchSavedPipelines();
      }
    } catch (e: any) {
      setAiError(e.message || 'Failed to save pipeline');
      showToast(e.message || 'Failed to save pipeline', 'error');
    }
  };

  // ── Visual Builder: Save as Pipeline ──
  const handleVisualSave = async () => {
    if (!selectedSource || transforms.length === 0) {
      setError('Add at least one transform step before saving.');
      return;
    }
    const pipeline: PipelineDef = {
      name: pipelineName || 'Untitled Pipeline',
      description: `Visual pipeline: ${transforms.length} step(s) on ${selectedSource}`,
      source: { type: 'file', file_name: selectedSource },
      steps: transforms.map((t, i) => ({
        id: t.id || `step_${i}`,
        type: t.type,
        description: t.description || `${t.type} step`,
        config: t.config,
      })),
      sink: { type: 'file', format: destFormat, file_name: destFilename || undefined },
      tags: ['visual-builder'],
    };
    try {
      const resp = await pipelineService.save(pipeline);
      if (resp.status === 'success') {
        showToast(`Pipeline saved: ${resp.name}`);
        fetchSavedPipelines();
      }
    } catch (e: any) {
      setError(e.message || 'Failed to save pipeline');
      showToast(e.message || 'Failed to save pipeline', 'error');
    }
  };

  // ── AI Pipeline: Download output ──
  const handleAiDownload = () => {
    if (!aiRun?.output_file) return;
    const url = pipelineService.getDownloadUrl(aiRun.output_file);
    window.open(url, '_blank');
  };

  // ── AI Pipeline: Edit steps in Visual Builder ──
  const handleEditInVisualBuilder = () => {
    if (!aiPipeline) return;
    // Convert AI pipeline steps → visual builder transforms
    const converted: ETLTransformStep[] = aiPipeline.steps.map((step, i) => ({
      id: step.id || `step_${Date.now()}_${i}`,
      type: step.type as TransformType,
      description: step.description || '',
      config: step.config || {},
    }));
    setTransforms(converted);
    // Use AI pipeline source file if available
    if (aiPipeline.source.file_name && aiPipeline.source.file_name !== selectedSource) {
      handleSourceChange(aiPipeline.source.file_name);
    }
    // Set destination format from sink
    if (aiPipeline.sink.format) {
      setDestFormat(aiPipeline.sink.format);
    }
    setPipelineName(aiPipeline.name || '');
    setActiveTab('visual');
  };

  /* ==============================================================
     RENDER
     ============================================================== */
  return (
    <div className="etl-panel">
      {/* ── Toast Notification ── */}
      {toast && (
        <div className={`etl-toast etl-toast--${toast.type}`}>
          <span>{toast.message}</span>
          <button className="etl-toast__close" onClick={() => setToast(null)}>✕</button>
        </div>
      )}

      {/* ── Header ── */}
      <div className="etl-header">
        <div className="etl-header__left">
          <span className="etl-header__icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>
          </span>
          <div>
            <h2 className="etl-header__title">Data Pipeline Builder</h2>
            <p className="etl-header__subtitle">Build pipelines with AI or the visual step editor</p>
          </div>
        </div>
        <div className="etl-header__tabs">
          <button
            className={`etl-tab ${activeTab === 'ai' ? 'etl-tab--active' : ''}`}
            onClick={() => setActiveTab('ai')}
          >
            AI Pipeline
          </button>
          <button
            className={`etl-tab ${activeTab === 'visual' ? 'etl-tab--active' : ''}`}
            onClick={() => setActiveTab('visual')}
          >
            Visual Builder
          </button>
          <button
            className={`etl-tab ${activeTab === 'saved' ? 'etl-tab--active' : ''}`}
            onClick={() => setActiveTab('saved')}
          >
            Saved
          </button>
        </div>
      </div>

      {error && activeTab !== 'ai' && (
        <div className="etl-error">
          {error}
          <button className="etl-error__close" onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {/* ── KPI Stats Bar ── */}
      {(() => {
        const outputRows = result?.output_rows ?? aiRun?.output_rows ?? null;
        const lastStatus = result?.status ?? aiRun?.status ?? null;
        const statusColor = lastStatus === 'success' ? '#34d399' : lastStatus === 'error' ? '#f87171' : 'var(--text-primary)';
        return (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 'var(--space-3)', flexShrink: 0 }}>
            {[
              { label: 'Source Files', value: String(sourceFiles.length), sub: 'available' },
              { label: 'Pipeline Steps', value: String(transforms.length), sub: 'build steps' },
              { label: 'Output Rows', value: outputRows != null ? outputRows.toLocaleString() : '—', sub: 'last run' },
              { label: 'Last Status', value: lastStatus ? lastStatus.charAt(0).toUpperCase() + lastStatus.slice(1) : '—', sub: 'execution', color: statusColor },
            ].map(({ label, value, sub, color }) => (
              <div key={label} style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-4) var(--space-5)' }}>
                <div style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--text-tertiary)', fontWeight: 600 }}>{label}</div>
                <div style={{ fontSize: 'var(--font-2xl)', fontWeight: 700, color: color || 'var(--text-primary)', fontFamily: 'var(--font-mono)', lineHeight: 1.1, marginTop: 4 }}>{value}</div>
                <div style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)', marginTop: 2 }}>{sub}</div>
              </div>
            ))}
          </div>
        );
      })()}

      {/* ═══════════════════════════════════════════════════════════
          AI Pipeline Tab
          ═══════════════════════════════════════════════════════════ */}
      {activeTab === 'ai' && (
        <div className="ai-pipeline">
          {/* Prompt input */}
          <div className="etl-section">
            <div className="etl-section__header">
              <span className="etl-step-badge">1</span>
              <span className="etl-section__title">Describe Your Pipeline</span>
            </div>
            <textarea
              className="etl-textarea ai-pipeline__prompt"
              placeholder="e.g., Read products.csv, filter items with rating above 4, sort by price descending, drop the stock column, and export as CSV"
              value={aiPrompt}
              onChange={e => setAiPrompt(e.target.value)}
              rows={4}
            />
            <div className="ai-pipeline__controls">
              {sourceFiles.length > 0 && (
                <select
                  className="etl-select ai-pipeline__source-select"
                  value={selectedSource}
                  onChange={e => setSelectedSource(e.target.value)}
                  disabled={kafkaEnabled}
                >
                  <option value="">Auto-detect source file</option>
                  {sourceFiles.map(f => <option key={f} value={f}>{f}</option>)}
                </select>
              )}
              <label className="ai-pipeline__kafka-toggle" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                <input
                  type="checkbox"
                  checked={kafkaEnabled}
                  onChange={e => setKafkaEnabled(e.target.checked)}
                />
                Use Kafka source
              </label>
              <button
                className="etl-btn etl-btn--primary"
                onClick={handleAiGenerate}
                disabled={aiGenerating || !aiPrompt.trim()}
              >
                {aiGenerating ? 'Generating…' : 'Generate Pipeline'}
              </button>
            </div>

            {kafkaEnabled && (
              <div
                className="ai-pipeline__kafka-form"
                style={{
                  marginTop: 12,
                  padding: 12,
                  border: '1px solid var(--border, #2a2f3a)',
                  borderRadius: 8,
                  display: 'grid',
                  gridTemplateColumns: 'repeat(2, minmax(180px, 1fr))',
                  gap: 10,
                }}
              >
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
                  Bootstrap servers
                  <input
                    className="etl-input"
                    value={kafkaBootstrap}
                    onChange={e => setKafkaBootstrap(e.target.value)}
                    placeholder="localhost:9092"
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
                  Topic
                  <input
                    className="etl-input"
                    value={kafkaTopic}
                    onChange={e => setKafkaTopic(e.target.value)}
                    placeholder="events"
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
                  Group ID (optional)
                  <input
                    className="etl-input"
                    value={kafkaGroupId}
                    onChange={e => setKafkaGroupId(e.target.value)}
                    placeholder="aura-pipeline"
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
                  Max messages
                  <input
                    className="etl-input"
                    type="number"
                    min={1}
                    value={kafkaMaxMessages}
                    onChange={e => setKafkaMaxMessages(parseInt(e.target.value, 10) || 1000)}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
                  Idle timeout (ms)
                  <input
                    className="etl-input"
                    type="number"
                    min={500}
                    step={500}
                    value={kafkaTimeoutMs}
                    onChange={e => setKafkaTimeoutMs(parseInt(e.target.value, 10) || 5000)}
                  />
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                  <input
                    type="checkbox"
                    checked={kafkaFromBeginning}
                    onChange={e => setKafkaFromBeginning(e.target.checked)}
                  />
                  Read from beginning
                </label>
              </div>
            )}
          </div>

          {aiError && (
            <div className="etl-error">
              {aiError}
              <button className="etl-error__close" onClick={() => setAiError(null)}>✕</button>
            </div>
          )}

          {/* Generated pipeline definition */}
          {aiPipeline && (
            <>
              <div className="etl-section">
                <div className="etl-section__header">
                  <span className="etl-step-badge">2</span>
                  <span className="etl-section__title">Generated Pipeline: {aiPipeline.name}</span>
                  {aiPipeline.tags?.includes('llm-free') && (
                    <span className="ai-pipeline__local-badge" title="Generated locally without LLM — no API calls, no rate limits">
                      Local
                    </span>
                  )}
                </div>
                {aiPipeline.description && (
                  <p className="ai-pipeline__desc">{aiPipeline.description}</p>
                )}

                {/* Source → Steps → Sink visual */}
                <div className="ai-pipeline__flow">
                  <div className="ai-pipeline__node ai-pipeline__node--source">
                    <span className="ai-pipeline__node-label">Source</span>
                    <span className="ai-pipeline__node-value">
                      {aiPipeline.source.type === 'file'
                        ? aiPipeline.source.file_name || 'file'
                        : `${aiPipeline.source.type}://${aiPipeline.source.table || 'query'}`}
                    </span>
                  </div>

                  {aiPipeline.steps.map((step, i) => (
                    <React.Fragment key={step.id || i}>
                      <span className="ai-pipeline__arrow">→</span>
                      <div className="ai-pipeline__node ai-pipeline__node--step">
                        <span className="ai-pipeline__node-label">
                          {step.type.replace(/_/g, ' ')}
                        </span>
                        <span className="ai-pipeline__node-value">{step.description}</span>
                      </div>
                    </React.Fragment>
                  ))}

                  <span className="ai-pipeline__arrow">→</span>
                  <div className="ai-pipeline__node ai-pipeline__node--sink">
                    <span className="ai-pipeline__node-label">Sink</span>
                    <span className="ai-pipeline__node-value">
                      {aiPipeline.sink.type === 'file'
                        ? aiPipeline.sink.format || 'csv'
                        : aiPipeline.sink.type === 'preview'
                          ? 'Preview'
                          : aiPipeline.sink.type}
                    </span>
                  </div>
                </div>

                {/* Step detail cards */}
                <div className="ai-pipeline__steps-detail">
                  {aiPipeline.steps.map((step, i) => (
                    <div key={step.id || i} className="ai-pipeline__step-card">
                      <span className="ai-pipeline__step-num">{i + 1}</span>
                      <div>
                        <strong>{step.type.replace(/_/g, ' ')}</strong>
                        {step.description && <span className="ai-pipeline__step-desc"> — {step.description}</span>}
                        <div className="ai-pipeline__step-config">
                          {Object.entries(step.config).map(([k, v]) => (
                            <span key={k} className="ai-pipeline__config-pill">
                              {k}: {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                            </span>
                          ))}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Execute buttons */}
              <div className="etl-actions">
                <button
                  className="etl-btn etl-btn--secondary"
                  onClick={() => handleAiExecute(true)}
                  disabled={aiExecuting}
                >
                  {aiExecuting ? 'Loading…' : 'Preview'}
                </button>
                <button
                  className="etl-btn etl-btn--primary etl-btn--lg"
                  onClick={() => handleAiExecute(false)}
                  disabled={aiExecuting}
                >
                  {aiExecuting ? 'Running…' : 'Execute Pipeline'}
                </button>
                <button
                  className="etl-btn etl-btn--ghost"
                  onClick={handleAiSave}
                >
                  Save Pipeline
                </button>
                <button
                  className="etl-btn etl-btn--ghost"
                  onClick={handleEditInVisualBuilder}
                >
                  Edit in Visual Builder
                </button>
              </div>
            </>
          )}

          {/* ── Live pipeline run (SSE) ── */}
          {aiRunId && (
            <div style={{ marginTop: 'var(--space-4)' }}>
              <PipelineMonitor runId={aiRunId} onComplete={handleAiRunComplete} />
            </div>
          )}

          {/* ── AI Run Result ── */}
          {aiRun && (
            <div className="etl-result">
              <div className="etl-result__header">
                <h3>
                  {aiRun.status === 'success' ? 'Pipeline Complete' : 'Pipeline Failed'}
                </h3>
                <div className="etl-result__stats">
                  <span className="etl-stat">
                    Source: <strong>{aiRun.rows_read.toLocaleString()}</strong> rows
                  </span>
                  <span className="etl-stat__arrow">→</span>
                  <span className="etl-stat">
                    Output: <strong>{aiRun.rows_written.toLocaleString()}</strong> rows
                  </span>
                  <span className="etl-stat etl-stat--muted">
                    {aiRun.steps_executed} steps · {aiRun.duration_ms.toFixed(0)}ms
                  </span>
                </div>
              </div>

              {aiRun.error && (
                <div className="etl-error">{aiRun.error}</div>
              )}

              {/* Output columns */}
              {aiRun.columns_out.length > 0 && (
                <div className="etl-schema-chips">
                  {aiRun.columns_out.map(col => (
                    <span key={col} className="etl-chip etl-chip--output">{col}</span>
                  ))}
                </div>
              )}

              {/* SQL Used */}
              {aiRun.sql_generated && (
                <details className="etl-sql-details">
                  <summary>Generated SQL</summary>
                  <pre className="etl-sql-pre">{aiRun.sql_generated}</pre>
                </details>
              )}

              {/* Preview Table */}
              {aiRun.preview_data && aiRun.preview_data.length > 0 && (
                <div className="etl-table-wrap">
                  <table className="etl-table etl-table--result">
                    <thead>
                      <tr>
                        {aiRun.columns_out.map(c => <th key={c}>{c}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {aiRun.preview_data.map((row, i) => (
                        <tr key={i}>
                          {aiRun.columns_out.map(c => (
                            <td key={c}>{String(row[c] ?? '')}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Download button */}
              {aiRun.output_file && (
                <div className="etl-download-row">
                  <button className="etl-btn etl-btn--primary" onClick={handleAiDownload}>
                    Download {aiRun.output_file}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════
          Visual / Natural Language Tabs (existing ETL)
          ═══════════════════════════════════════════════════════════ */}
      {activeTab !== 'ai' && activeTab !== 'saved' && (
        <>

      {/* ── Pipeline Name ── */}
      <div className="etl-section">
        <label className="etl-label">Pipeline Name</label>
        <input
          className="etl-input"
          placeholder="e.g., Clean Products Data"
          value={pipelineName}
          onChange={e => setPipelineName(e.target.value)}
        />
      </div>

      {/* ── STEP 1: Source ── */}
      <div className="etl-section">
        <div className="etl-section__header">
          <span className="etl-step-badge">1</span>
          <span className="etl-section__title">Extract — Source File</span>
        </div>
        <div className="etl-source-row">
          <select
            className="etl-select"
            value={selectedSource}
            onChange={e => handleSourceChange(e.target.value)}
          >
            <option value="">Select an uploaded file…</option>
            {sourceFiles.map(f => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
          <button
            className="etl-btn etl-btn--ghost"
            onClick={fetchSourceFiles}
            title="Refresh file list"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          </button>
        </div>

        {isLoading && !result && !sourcePreview && (
          <div className="etl-loading">Loading source preview…</div>
        )}

        {sourcePreview && (
          <div className="etl-source-info">
            <div className="etl-source-stats">
              <span className="etl-stat">
                <strong>{sourcePreview.row_count.toLocaleString()}</strong> rows
              </span>
              <span className="etl-stat">
                <strong>{sourcePreview.columns.length}</strong> columns
              </span>
              <span className="etl-stat etl-stat--muted">
                table: <code>{sourcePreview.table_name}</code>
              </span>
            </div>
            <div className="etl-schema-chips">
              {sourcePreview.columns.map(col => (
                <span key={col.name} className="etl-chip" title={col.type}>
                  {col.name} <span className="etl-chip__type">{col.type}</span>
                </span>
              ))}
            </div>
            {sourcePreview.preview.length > 0 && (
              <details className="etl-preview-details">
                <summary>Preview first {sourcePreview.preview.length} rows</summary>
                <div className="etl-table-wrap">
                  <table className="etl-table">
                    <thead>
                      <tr>
                        {sourcePreview.columns.map(c => <th key={c.name}>{c.name}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {sourcePreview.preview.map((row, i) => (
                        <tr key={i}>
                          {sourcePreview.columns.map(c => (
                            <td key={c.name}>{String(row[c.name] ?? '')}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            )}
          </div>
        )}
      </div>

      {/* ── STEP 2: Transform ── */}
      <div className="etl-section">
        <div className="etl-section__header">
          <span className="etl-step-badge">2</span>
          <span className="etl-section__title">Transform — Processing Steps</span>
          <span className="etl-step-count">{transforms.length} step{transforms.length !== 1 ? 's' : ''}</span>
        </div>

        {/* Transform steps list */}
        <div className="etl-steps-list">
          {transforms.length === 0 ? (
            <div className="etl-empty-steps">
              <p>No transform steps yet.</p>
              <p className="etl-empty-hint">
                Click "Add Step" below, or use the AI Pipeline tab to generate steps automatically.
              </p>
            </div>
          ) : (
            transforms.map((step, idx) => (
              <TransformStepCard
                key={step.id}
                step={step}
                index={idx}
                columns={sourcePreview?.columns || []}
                total={transforms.length}
                onUpdate={(updates) => updateTransform(step.id, updates)}
                onRemove={() => removeTransform(step.id)}
                onMove={(dir) => moveTransform(idx, dir)}
              />
            ))
          )}
        </div>

        {/* Add Step grid — always shown in visual builder */}
          <div className="etl-add-step-area">
            {showAddStep ? (
              <div className="etl-add-step-grid">
                {TRANSFORM_TYPES.map(t => (
                  <button
                    key={t.value}
                    className="etl-add-step-btn"
                    onClick={() => addTransform(t.value)}
                  >
                    <span className="etl-add-step-btn__icon">{t.icon}</span>
                    <span>{t.label}</span>
                  </button>
                ))}
                <button
                  className="etl-add-step-btn etl-add-step-btn--cancel"
                  onClick={() => setShowAddStep(false)}
                >
                  ✕ Cancel
                </button>
              </div>
            ) : (
              <button
                className="etl-btn etl-btn--dashed"
                onClick={() => setShowAddStep(true)}
                disabled={!selectedSource}
              >
                + Add Transform Step
              </button>
            )}
          </div>
      </div>

      {/* ── STEP 3: Destination ── */}
      <div className="etl-section">
        <div className="etl-section__header">
          <span className="etl-step-badge">3</span>
          <span className="etl-section__title">Load — Destination</span>
        </div>
        <div className="etl-dest-row">
          <div className="etl-dest-formats">
            {DEST_FORMATS.map(f => (
              <button
                key={f.value}
                className={`etl-format-btn ${destFormat === f.value ? 'etl-format-btn--active' : ''}`}
                onClick={() => setDestFormat(f.value)}
              >
                <span>{f.icon}</span> {f.label}
              </button>
            ))}
          </div>
          <input
            className="etl-input etl-input--filename"
            placeholder="Output filename (optional)"
            value={destFilename}
            onChange={e => setDestFilename(e.target.value)}
          />
        </div>
      </div>

      {/* ── Execute ── */}
      <div className="etl-actions">
        <button
          className="etl-btn etl-btn--secondary"
          onClick={() => executePipeline(true)}
          disabled={isLoading || !selectedSource}
        >
          {isLoading ? 'Loading…' : 'Preview Result'}
        </button>
        <button
          className="etl-btn etl-btn--primary etl-btn--lg"
          onClick={() => executePipeline(false)}
          disabled={isLoading || !selectedSource}
        >
          {isLoading ? 'Running…' : 'Execute Pipeline'}
        </button>
        <button
          className="etl-btn etl-btn--ghost"
          onClick={handleVisualSave}
          disabled={!selectedSource || transforms.length === 0}
        >
          Save Pipeline
        </button>
      </div>

      {/* ── Result ── */}
      {result && (
        <div className="etl-result">
          <div className="etl-result__header">
            <h3>
              {result.preview_only ? 'Preview Result' : 'Pipeline Complete'}
            </h3>
            <div className="etl-result__stats">
              <span className="etl-stat">
                Source: <strong>{result.source.row_count.toLocaleString()}</strong> rows
              </span>
              <span className="etl-stat__arrow">→</span>
              <span className="etl-stat">
                Output: <strong>{result.output.row_count.toLocaleString()}</strong> rows
              </span>
              <span className="etl-stat etl-stat--muted">
                {result.transforms_applied} transform{result.transforms_applied !== 1 ? 's' : ''} · {result.execution_time_ms.toFixed(0)}ms
              </span>
            </div>
          </div>

          {/* Output columns */}
          <div className="etl-schema-chips">
            {result.output.columns.map(col => (
              <span key={col.name} className="etl-chip etl-chip--output" title={col.type}>
                {col.name} <span className="etl-chip__type">{col.type}</span>
              </span>
            ))}
          </div>

          {/* SQL Used */}
          {result.transform_sql && (
            <details className="etl-sql-details">
              <summary>Generated SQL</summary>
              <pre className="etl-sql-pre">{result.transform_sql}</pre>
            </details>
          )}

          {/* Preview Table */}
          {result.preview.length > 0 && (
            <div className="etl-table-wrap">
              <table className="etl-table etl-table--result">
                <thead>
                  <tr>
                    {result.output.columns.map(c => <th key={c.name}>{c.name}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {result.preview.map((row, i) => (
                    <tr key={i}>
                      {result.output.columns.map(c => (
                        <td key={c.name}>{String(row[c.name] ?? '')}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Download button */}
          {!result.preview_only && result.output.file && (
            <div className="etl-download-row">
              <button className="etl-btn etl-btn--primary" onClick={handleDownload}>
                Download {result.output.file}
              </button>
            </div>
          )}
        </div>
      )}

        </>
      )}

      {/* ═══════════════════════════════════════════════════════════
          Saved Pipelines Tab
          ═══════════════════════════════════════════════════════════ */}
      {activeTab === 'saved' && (
        <div className="saved-pipelines">
          {/* Templates Section */}
          <div className="etl-section">
            <div className="etl-section__header">
              <span className="etl-step-badge">~</span>
              <span className="etl-section__title">Quick-Start Templates</span>
              <span className="etl-step-count">{PIPELINE_TEMPLATES.length} templates</span>
            </div>
            <div className="saved-pipelines__templates-grid">
              {PIPELINE_TEMPLATES.map(t => (
                <button
                  key={t.name}
                  className="saved-pipelines__template-card"
                  onClick={() => handleUseTemplate(t)}
                >
                  <span className="saved-pipelines__template-icon">{t.icon}</span>
                  <span className="saved-pipelines__template-name">{t.name}</span>
                  <span className="saved-pipelines__template-desc">{t.description}</span>
                  <div className="saved-pipelines__template-tags">
                    {t.tags.map(tag => (
                      <span key={tag} className="saved-pipelines__tag">{tag}</span>
                    ))}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Saved Pipelines List */}
          <div className="etl-section">
            <div className="etl-section__header">
              <span className="etl-step-badge">#</span>
              <span className="etl-section__title">Saved Pipelines</span>
              <span className="etl-step-count">{savedPipelines.length} pipeline{savedPipelines.length !== 1 ? 's' : ''}</span>
              <button
                className="etl-btn etl-btn--ghost"
                onClick={fetchSavedPipelines}
                disabled={savedLoading}
                title="Refresh"
                style={{ marginLeft: 'auto' }}
              >
                {savedLoading ? '…' : 'Refresh'}
              </button>
            </div>

            {savedError && (
              <div className="etl-error">
                ⚠️ {savedError}
                <button className="etl-error__close" onClick={() => setSavedError(null)}>✕</button>
              </div>
            )}

            {savedLoading && savedPipelines.length === 0 && (
              <div className="etl-loading">Loading saved pipelines…</div>
            )}

            {!savedLoading && savedPipelines.length === 0 && !savedError && (
              <div className="saved-pipelines__empty">
                <span className="saved-pipelines__empty-icon"></span>
                <p className="saved-pipelines__empty-title">No saved pipelines yet</p>
                <p className="saved-pipelines__empty-text">
                  Generate a pipeline with AI or build one manually, then click "Save Pipeline" to store it here.
                </p>
                <button
                  className="etl-btn etl-btn--primary"
                  onClick={() => setActiveTab('ai')}
                >
                  Create with AI
                </button>
              </div>
            )}

            {savedPipelines.length > 0 && (
              <div className="saved-pipelines__list">
                {savedPipelines.map(p => (
                  <div key={p.id} className="saved-pipelines__card">
                    <div className="saved-pipelines__card-header">
                      <div className="saved-pipelines__card-info">
                        <h4 className="saved-pipelines__card-name">{p.name}</h4>
                        {p.description && (
                          <p className="saved-pipelines__card-desc">{p.description}</p>
                        )}
                      </div>
                      <span className={`saved-pipelines__status saved-pipelines__status--${p.status}`}>
                        {p.status}
                      </span>
                    </div>
                    <div className="saved-pipelines__card-meta">
                      <span className="saved-pipelines__meta-item">{p.source}</span>
                      <span className="saved-pipelines__meta-item">{p.steps} step{p.steps !== 1 ? 's' : ''}</span>
                      <span className="saved-pipelines__meta-item">{p.sink}</span>
                      {p.created_at && (
                        <span className="saved-pipelines__meta-item saved-pipelines__meta-item--muted">
                          {new Date(p.created_at).toLocaleDateString()}
                        </span>
                      )}
                    </div>
                    {p.tags.length > 0 && (
                      <div className="saved-pipelines__card-tags">
                        {p.tags.map(tag => (
                          <span key={tag} className="saved-pipelines__tag">{tag}</span>
                        ))}
                      </div>
                    )}
                    <div className="saved-pipelines__card-actions">
                      <button
                        className="etl-btn etl-btn--primary"
                        onClick={() => handleLoadPipeline(p.id)}
                      >
                        Load & Run
                      </button>
                      <button
                        className="etl-btn etl-btn--secondary saved-pipelines__delete-btn"
                        onClick={() => handleDeletePipeline(p.id, p.name)}
                        disabled={deletingId === p.id}
                      >
                        {deletingId === p.id ? 'Deleting…' : 'Delete'}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

/* ================================================================
   TransformStepCard — config card for one transform step
   ================================================================ */

interface TransformStepCardProps {
  step: ETLTransformStep;
  index: number;
  columns: ETLColumnSchema[];
  total: number;
  onUpdate: (updates: Partial<ETLTransformStep>) => void;
  onRemove: () => void;
  onMove: (dir: 'up' | 'down') => void;
}

const TransformStepCard: React.FC<TransformStepCardProps> = ({
  step, index, columns, total, onUpdate, onRemove, onMove,
}) => {
  const meta = TRANSFORM_TYPES.find(t => t.value === step.type);

  const updateConfig = (key: string, value: any) => {
    onUpdate({ config: { ...step.config, [key]: value } });
  };

  return (
    <div className="etl-step-card">
      <div className="etl-step-card__header">
        <span className="etl-step-card__badge">{index + 1}</span>
        <span className="etl-step-card__icon">{meta?.icon}</span>
        <span className="etl-step-card__label">{meta?.label || step.type}</span>
        <div className="etl-step-card__actions">
          <button onClick={() => onMove('up')} disabled={index === 0} title="Move up">↑</button>
          <button onClick={() => onMove('down')} disabled={index === total - 1} title="Move down">↓</button>
          <button onClick={onRemove} className="etl-step-card__remove" title="Remove">✕</button>
        </div>
      </div>

      {/* Description */}
      <input
        className="etl-input etl-input--sm"
        placeholder="Step description (optional)"
        value={step.description}
        onChange={e => onUpdate({ description: e.target.value })}
      />

      {/* Type-specific config */}
      <div className="etl-step-card__config">
        {step.type === 'filter' && (
          <div className="etl-config-field">
            <label>Condition (SQL WHERE clause)</label>
            <input
              className="etl-input"
              placeholder='e.g., price > 10 AND category IS NOT NULL'
              value={step.config.condition || ''}
              onChange={e => updateConfig('condition', e.target.value)}
            />
          </div>
        )}

        {step.type === 'sort' && (
          <div className="etl-config-row">
            <div className="etl-config-field">
              <label>Column</label>
              <select
                className="etl-select"
                value={step.config.column || ''}
                onChange={e => updateConfig('column', e.target.value)}
              >
                <option value="">Select…</option>
                {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </div>
            <div className="etl-config-field">
              <label>Order</label>
              <select
                className="etl-select"
                value={step.config.order || 'ASC'}
                onChange={e => updateConfig('order', e.target.value)}
              >
                <option value="ASC">Ascending</option>
                <option value="DESC">Descending</option>
              </select>
            </div>
          </div>
        )}

        {step.type === 'drop_columns' && (
          <div className="etl-config-field">
            <label>Columns to drop (comma-separated)</label>
            <input
              className="etl-input"
              placeholder="col1, col2"
              value={(step.config.columns || []).join(', ')}
              onChange={e => updateConfig('columns', e.target.value.split(',').map(s => s.trim()).filter(Boolean))}
            />
            <div className="etl-available-cols">
              {columns.map(c => (
                <button
                  key={c.name}
                  className={`etl-col-toggle ${(step.config.columns || []).includes(c.name) ? 'etl-col-toggle--active' : ''}`}
                  onClick={() => {
                    const current: string[] = step.config.columns || [];
                    const updated = current.includes(c.name)
                      ? current.filter(x => x !== c.name)
                      : [...current, c.name];
                    updateConfig('columns', updated);
                  }}
                >
                  {c.name}
                </button>
              ))}
            </div>
          </div>
        )}

        {step.type === 'rename' && (
          <div className="etl-config-field">
            <label>Rename mappings (one per line: old_name → new_name)</label>
            <textarea
              className="etl-textarea etl-textarea--sm"
              placeholder={'old_column → new_column\nprice → unit_price'}
              value={
                Object.entries(step.config.mappings || {})
                  .map(([o, n]) => `${o} → ${n}`)
                  .join('\n')
              }
              onChange={e => {
                const mappings: Record<string, string> = {};
                e.target.value.split('\n').forEach(line => {
                  const parts = line.split(/→|->|=>/).map(s => s.trim());
                  if (parts.length === 2 && parts[0] && parts[1]) {
                    mappings[parts[0]] = parts[1];
                  }
                });
                updateConfig('mappings', mappings);
              }}
              rows={3}
            />
          </div>
        )}

        {step.type === 'add_column' && (
          <div className="etl-config-row">
            <div className="etl-config-field">
              <label>Column Name</label>
              <input
                className="etl-input"
                placeholder="new_column"
                value={step.config.name || ''}
                onChange={e => updateConfig('name', e.target.value)}
              />
            </div>
            <div className="etl-config-field etl-config-field--grow">
              <label>Expression (SQL)</label>
              <input
                className="etl-input"
                placeholder="e.g., price * quantity"
                value={step.config.expression || ''}
                onChange={e => updateConfig('expression', e.target.value)}
              />
            </div>
          </div>
        )}

        {step.type === 'aggregate' && (
          <>
            <div className="etl-config-field">
              <label>Group By columns (comma-separated)</label>
              <input
                className="etl-input"
                placeholder="category, region"
                value={(step.config.group_by || []).join(', ')}
                onChange={e => updateConfig('group_by', e.target.value.split(',').map(s => s.trim()).filter(Boolean))}
              />
            </div>
            <div className="etl-config-field">
              <label>Aggregations (one per line: FUNC(column) AS alias)</label>
              <textarea
                className="etl-textarea etl-textarea--sm"
                placeholder={'SUM(amount) AS total\nAVG(price) AS avg_price\nCOUNT(*) AS count'}
                value={
                  (step.config.aggregations || [])
                    .map((a: any) => `${a.func}(${a.column}) AS ${a.alias || a.column}`)
                    .join('\n')
                }
                onChange={e => {
                  const aggs = e.target.value.split('\n')
                    .map(line => {
                      const m = line.match(/^(\w+)\(([^)]+)\)\s+AS\s+(\w+)/i);
                      if (m) return { func: m[1].toUpperCase(), column: m[2].trim(), alias: m[3].trim() };
                      return null;
                    })
                    .filter(Boolean);
                  updateConfig('aggregations', aggs);
                }}
                rows={3}
              />
            </div>
          </>
        )}

        {step.type === 'deduplicate' && (
          <div className="etl-config-field">
            <label>Deduplicate by columns (empty = all columns)</label>
            <input
              className="etl-input"
              placeholder="id, email (leave empty for full dedup)"
              value={(step.config.columns || []).join(', ')}
              onChange={e => updateConfig('columns', e.target.value.split(',').map(s => s.trim()).filter(Boolean))}
            />
          </div>
        )}

        {step.type === 'cast_type' && (
          <div className="etl-config-row">
            <div className="etl-config-field">
              <label>Column</label>
              <select
                className="etl-select"
                value={step.config.column || ''}
                onChange={e => updateConfig('column', e.target.value)}
              >
                <option value="">Select…</option>
                {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </div>
            <div className="etl-config-field">
              <label>Target Type</label>
              <select
                className="etl-select"
                value={step.config.to_type || 'VARCHAR'}
                onChange={e => updateConfig('to_type', e.target.value)}
              >
                {['VARCHAR', 'INTEGER', 'BIGINT', 'DOUBLE', 'FLOAT', 'BOOLEAN', 'DATE', 'TIMESTAMP'].map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
          </div>
        )}

        {step.type === 'fill_missing' && (
          <div className="etl-config-row" style={{ flexWrap: 'wrap' }}>
            <div className="etl-config-field">
              <label>Column</label>
              <select
                className="etl-select"
                value={step.config.column || ''}
                onChange={e => updateConfig('column', e.target.value)}
              >
                <option value="">Select…</option>
                <option value="*">✦ All Columns</option>
                {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </div>
            <div className="etl-config-field">
              <label>Strategy</label>
              <select
                className="etl-select"
                value={step.config.strategy || 'value'}
                onChange={e => updateConfig('strategy', e.target.value)}
              >
                <option value="value">Fixed Value</option>
                <option value="mean">Mean (numeric cols)</option>
                <option value="median">Median (numeric cols)</option>
              </select>
            </div>
            <div className="etl-config-field etl-config-field--grow">
              <label>{(step.config.strategy === 'mean' || step.config.strategy === 'median') ? 'Fallback for text columns' : 'Fill Value (SQL expression)'}</label>
              <input
                className="etl-input"
                placeholder={step.config.column === '*' ? '0  (numeric default, text → N/A)' : "0 or 'Unknown' or AVG(col)"}
                value={step.config.value || ''}
                onChange={e => updateConfig('value', e.target.value)}
              />
            </div>
          </div>
        )}

        {step.type === 'custom_sql' && (
          <div className="etl-config-field">
            <label>Custom SQL (use {'{{input}}'} for previous step)</label>
            <textarea
              className="etl-textarea"
              placeholder={'SELECT *, price * 0.9 AS discounted_price FROM {{input}}'}
              value={step.config.sql || ''}
              onChange={e => updateConfig('sql', e.target.value)}
              rows={4}
            />
          </div>
        )}
      </div>
    </div>
  );
};

/* ================================================================
   Helpers
   ================================================================ */

function getDefaultConfig(type: TransformType): Record<string, any> {
  switch (type) {
    case 'filter': return { condition: '' };
    case 'sort': return { column: '', order: 'ASC' };
    case 'drop_columns': return { columns: [] };
    case 'rename': return { mappings: {} };
    case 'add_column': return { name: '', expression: '' };
    case 'aggregate': return { group_by: [], aggregations: [] };
    case 'deduplicate': return { columns: [] };
    case 'cast_type': return { column: '', to_type: 'VARCHAR' };
    case 'fill_missing': return { column: '', value: '', strategy: 'value' };
    case 'custom_sql': return { sql: '' };
    default: return {};
  }
}

export default PipelinesPanel;
