import React, { useState, useEffect, useCallback } from 'react';
import { RefreshCw, Settings } from 'lucide-react';
import { type PageType } from '../lib/pageTypes';
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
import PipelineMonitor, { type PipelineRunSummary } from '../components/PipelineMonitor';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { cn } from '@/lib/cn';

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
   Style tokens — shared Tailwind class strings composed from the
   frontend/CLAUDE.md token vocabulary (bg-card / bg-secondary /
   border-border, text-text-secondary / text-text-tertiary /
   text-card-foreground, signal/info/warn/danger accents). Kept as
   module-scope constants so the JSX below stays readable across the
   many repeated card/chip/table shapes in this panel.
   ================================================================ */

const SECTION_HEADER = 'flex flex-wrap items-center gap-2 border-b border-border pb-2';
const SECTION_TITLE = 'text-sm font-semibold text-card-foreground';
const STEP_BADGE = 'flex size-5 shrink-0 items-center justify-center rounded-none border border-signal/40 bg-signal/10 font-mono text-2xs font-bold text-signal';
const STEP_COUNT = 'ml-auto font-mono text-2xs text-text-tertiary';
const FORM_LABEL = 'mb-1.5 block font-mono text-2xs font-semibold uppercase tracking-wide text-text-tertiary';
const FIELD_LABEL = 'font-mono text-2xs font-semibold text-text-tertiary';
const INPUT = 'w-full rounded-none border border-border bg-secondary px-3 py-2 text-sm text-card-foreground outline-none placeholder:text-text-tertiary focus:border-signal';
const INPUT_SM = 'w-full rounded-none border border-border bg-secondary px-2 py-1.5 text-xs text-card-foreground outline-none placeholder:text-text-tertiary focus:border-signal';
const SELECT = `${INPUT} cursor-pointer`;
const TEXTAREA = `${INPUT} resize-y leading-relaxed`;
const TEXTAREA_SM = `${INPUT_SM} resize-y leading-relaxed`;
const CONFIG_FIELD = 'flex flex-col gap-1.5';
const CONFIG_ROW = 'flex flex-wrap gap-3';
const CHIP = 'inline-flex items-center gap-1 rounded-none border border-border bg-card px-2 py-0.5 font-mono text-2xs text-text-secondary';
const CHIP_OUTPUT = 'inline-flex items-center gap-1 rounded-none border border-signal/40 bg-signal/10 px-2 py-0.5 font-mono text-2xs text-signal';
const STAT = 'text-sm text-text-secondary';
const STAT_MUTED = 'text-xs text-text-tertiary';
const ERROR_BANNER = 'flex items-center gap-2 rounded-none border border-danger/40 bg-danger/10 px-4 py-2.5 font-mono text-xs text-danger';
const RESULT_WRAP = 'rounded-none border border-border bg-secondary';
const RESULT_HEADER = 'flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card px-4 py-2.5';
const TABLE_WRAP = 'max-h-[280px] overflow-auto rounded-none border border-border';
const TABLE = 'w-full border-collapse text-xs';
const TH = 'whitespace-nowrap border-b border-border bg-card px-3 py-2 text-left font-mono text-2xs font-semibold uppercase tracking-wide text-text-tertiary';
const TH_RESULT = 'whitespace-nowrap border-b border-signal/30 bg-signal/10 px-3 py-2 text-left font-mono text-2xs font-semibold uppercase tracking-wide text-signal';
const TD = 'border-b border-border px-3 py-2 font-mono text-text-secondary';
const TAB_BASE = 'rounded-none border px-3 py-1.5 font-mono text-2xs font-semibold uppercase tracking-wide transition-colors';
const TAB_ACTIVE = 'border-signal/40 bg-signal/10 text-signal';
const TAB_INACTIVE = 'border-transparent text-text-tertiary hover:bg-accent hover:text-text-secondary';
const FORMAT_BASE = 'flex items-center gap-1.5 rounded-none border border-border bg-card px-3 py-1.5 font-mono text-xs font-bold text-text-tertiary transition-colors hover:text-text-secondary';
const FORMAT_ACTIVE = 'border-signal/40 bg-signal/10 text-signal';
const ADD_STEP_BTN = 'flex flex-col items-center gap-1 rounded-none border border-border bg-card px-2 py-2.5 font-mono text-2xs text-text-secondary transition-colors hover:border-signal/40 hover:bg-signal/10 hover:text-signal';
const COL_TOGGLE_BASE = 'rounded-none border border-border bg-card px-2 py-0.5 font-mono text-2xs text-text-tertiary transition-colors hover:border-signal/40 hover:text-text-secondary';
const COL_TOGGLE_ACTIVE = 'border-signal/40 bg-signal/10 text-signal';
const TEMPLATE_CARD = 'flex flex-col gap-2 rounded-none border border-border bg-secondary p-4 text-left transition-colors hover:border-signal/40 hover:bg-signal/10';

function resultStatusClass(status: string | null): string {
  if (status === 'success') return 'text-signal';
  if (status === 'error') return 'text-danger';
  return 'text-card-foreground';
}

function savedStatusClass(status: string): string {
  switch (status) {
    case 'active': return 'border-signal/40 bg-signal/10 text-signal';
    case 'inactive': return 'border-warn/40 bg-warn/10 text-warn';
    case 'error': return 'border-danger/40 bg-danger/10 text-danger';
    default: return 'border-border bg-card text-text-tertiary';
  }
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
    try {
      const res = await etlService.execute(payload);
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
    try {
      const resp = await pipelineService.generate(aiPrompt.trim(), selectedSource || undefined);
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
    <div className="flex h-full min-h-0 flex-col gap-4">
      {/* ── Toast Notification ── */}
      {toast && (
        <div
          className={cn(
            'fixed bottom-5 right-5 z-50 flex max-w-sm items-center gap-3 rounded-none border px-4 py-3 font-mono text-xs',
            toast.type === 'success'
              ? 'border-signal/40 bg-signal/10 text-signal'
              : 'border-danger/40 bg-danger/10 text-danger',
          )}
        >
          <span>{toast.message}</span>
          <button
            aria-label="Dismiss"
            title="Dismiss"
            onClick={() => setToast(null)}
            className="ml-auto opacity-60 hover:opacity-100"
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>
      )}

      {/* ── Header ── */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-none border border-border bg-card px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-none border border-signal/40 bg-signal/10 text-signal">
            <Settings className="size-4" />
          </span>
          <div>
            <h2 className="font-display text-base font-semibold text-card-foreground">Data Pipeline Builder</h2>
            <p className="mt-0.5 text-xs text-text-tertiary">Build pipelines with AI or the visual step editor</p>
          </div>
        </div>
        <div className="flex gap-1">
          <button
            type="button"
            className={cn(TAB_BASE, activeTab === 'ai' ? TAB_ACTIVE : TAB_INACTIVE)}
            onClick={() => setActiveTab('ai')}
          >
            AI Pipeline
          </button>
          <button
            type="button"
            className={cn(TAB_BASE, activeTab === 'visual' ? TAB_ACTIVE : TAB_INACTIVE)}
            onClick={() => setActiveTab('visual')}
          >
            Visual Builder
          </button>
          <button
            type="button"
            className={cn(TAB_BASE, activeTab === 'saved' ? TAB_ACTIVE : TAB_INACTIVE)}
            onClick={() => setActiveTab('saved')}
          >
            Saved
          </button>
        </div>
      </div>

      {error && activeTab !== 'ai' && (
        <div className={ERROR_BANNER}>
          <span className="flex-1">{error}</span>
          <button aria-label="Dismiss" title="Dismiss" onClick={() => setError(null)} className="opacity-60 hover:opacity-100">
            <span aria-hidden="true">✕</span>
          </button>
        </div>
      )}

      {/* ── KPI Stats Bar ── */}
      {(() => {
        const outputRows = result?.output.row_count ?? aiRun?.rows_written ?? null;
        const lastStatus = result?.status ?? aiRun?.status ?? null;
        const statusClass = resultStatusClass(lastStatus);
        const cards: { label: string; value: string; sub: string; colorClass?: string }[] = [
          { label: 'Source Files', value: String(sourceFiles.length), sub: 'available' },
          { label: 'Pipeline Steps', value: String(transforms.length), sub: 'build steps' },
          { label: 'Output Rows', value: outputRows != null ? outputRows.toLocaleString() : '—', sub: 'last run' },
          { label: 'Last Status', value: lastStatus ? lastStatus.charAt(0).toUpperCase() + lastStatus.slice(1) : '—', sub: 'execution', colorClass: statusClass },
        ];
        return (
          <div className="grid shrink-0 grid-cols-[repeat(auto-fit,minmax(min(140px,100%),1fr))] gap-3">
            {cards.map(({ label, value, sub, colorClass }) => (
              <div key={label} className="rounded-none border border-border bg-card px-5 py-4">
                <div className="font-mono text-2xs font-semibold uppercase tracking-wide text-text-tertiary">{label}</div>
                <div className={cn('mt-1 font-mono text-2xl font-bold leading-none', colorClass || 'text-card-foreground')}>{value}</div>
                <div className="mt-0.5 text-xs text-text-tertiary">{sub}</div>
              </div>
            ))}
          </div>
        );
      })()}

      {/* ═══════════════════════════════════════════════════════════
          AI Pipeline Tab
          ═══════════════════════════════════════════════════════════ */}
      {activeTab === 'ai' && (
        <div className="flex flex-col gap-4">
          {/* Prompt input */}
          <div className="flex flex-col gap-3">
            <div className={SECTION_HEADER}>
              <span className={STEP_BADGE}>1</span>
              <span className={SECTION_TITLE}>Describe Your Pipeline</span>
            </div>
            <textarea
              className={cn(TEXTAREA, 'min-h-[90px]')}
              placeholder="e.g., Read products.csv, filter items with rating above 4, sort by price descending, drop the stock column, and export as CSV"
              value={aiPrompt}
              onChange={e => setAiPrompt(e.target.value)}
              rows={4}
            />
            <div className="flex flex-wrap items-center gap-3">
              {sourceFiles.length > 0 && (
                <select
                  className={cn(SELECT, 'max-w-[260px]')}
                  value={selectedSource}
                  onChange={e => setSelectedSource(e.target.value)}
                  disabled={kafkaEnabled}
                >
                  <option value="">Auto-detect source file</option>
                  {sourceFiles.map(f => <option key={f} value={f}>{f}</option>)}
                </select>
              )}
              <label className="flex items-center gap-1.5 text-sm text-card-foreground">
                <input
                  type="checkbox"
                  checked={kafkaEnabled}
                  onChange={e => setKafkaEnabled(e.target.checked)}
                  className="accent-signal"
                />
                Use Kafka source
              </label>
              <Button type="button" onClick={handleAiGenerate} disabled={aiGenerating || !aiPrompt.trim()}>
                {aiGenerating ? 'Generating…' : 'Generate Pipeline'}
              </Button>
            </div>

            {kafkaEnabled && (
              <div className="mt-1 grid grid-cols-[repeat(auto-fit,minmax(min(180px,100%),1fr))] gap-2.5 rounded-none border border-border p-3">
                <label className="flex flex-col gap-1 text-xs text-text-secondary">
                  Bootstrap servers
                  <input
                    className={INPUT}
                    value={kafkaBootstrap}
                    onChange={e => setKafkaBootstrap(e.target.value)}
                    placeholder="localhost:9092"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-text-secondary">
                  Topic
                  <input
                    className={INPUT}
                    value={kafkaTopic}
                    onChange={e => setKafkaTopic(e.target.value)}
                    placeholder="events"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-text-secondary">
                  Group ID (optional)
                  <input
                    className={INPUT}
                    value={kafkaGroupId}
                    onChange={e => setKafkaGroupId(e.target.value)}
                    placeholder="aura-pipeline"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-text-secondary">
                  Max messages
                  <input
                    className={INPUT}
                    type="number"
                    min={1}
                    value={kafkaMaxMessages}
                    onChange={e => setKafkaMaxMessages(parseInt(e.target.value, 10) || 1000)}
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-text-secondary">
                  Idle timeout (ms)
                  <input
                    className={INPUT}
                    type="number"
                    min={500}
                    step={500}
                    value={kafkaTimeoutMs}
                    onChange={e => setKafkaTimeoutMs(parseInt(e.target.value, 10) || 5000)}
                  />
                </label>
                <label className="flex items-center gap-1.5 text-xs text-text-secondary">
                  <input
                    type="checkbox"
                    checked={kafkaFromBeginning}
                    onChange={e => setKafkaFromBeginning(e.target.checked)}
                    className="accent-signal"
                  />
                  Read from beginning
                </label>
              </div>
            )}
          </div>

          {aiError && (
            <div className={ERROR_BANNER}>
              <span className="flex-1">{aiError}</span>
              <button aria-label="Dismiss" title="Dismiss" onClick={() => setAiError(null)} className="opacity-60 hover:opacity-100">
                <span aria-hidden="true">✕</span>
              </button>
            </div>
          )}

          {/* Generated pipeline definition */}
          {aiPipeline && (
            <>
              <div className="flex flex-col gap-3">
                <div className={SECTION_HEADER}>
                  <span className={STEP_BADGE}>2</span>
                  <span className={SECTION_TITLE}>Generated Pipeline: {aiPipeline.name}</span>
                  {aiPipeline.tags?.includes('llm-free') && (
                    <span
                      className="rounded-none border border-warn/40 bg-warn/10 px-1.5 py-0.5 font-mono text-2xs font-bold text-warn"
                      title="Generated locally without LLM — no API calls, no rate limits"
                    >
                      Local
                    </span>
                  )}
                </div>
                {aiPipeline.description && (
                  <p className="text-sm text-text-tertiary">{aiPipeline.description}</p>
                )}

                {/* Source → Steps → Sink visual */}
                <div className="flex flex-wrap items-center gap-2 rounded-none border border-border bg-secondary p-4">
                  <div className="flex min-w-[90px] flex-col gap-1 rounded-none border border-info/40 bg-info/10 px-3 py-2.5">
                    <span className="font-mono text-2xs font-bold uppercase tracking-wide text-text-tertiary">Source</span>
                    <span className="break-all font-mono text-2xs text-text-secondary">
                      {aiPipeline.source.type === 'file'
                        ? aiPipeline.source.file_name || 'file'
                        : `${aiPipeline.source.type}://${aiPipeline.source.table || 'query'}`}
                    </span>
                  </div>

                  {aiPipeline.steps.map((step, i) => (
                    <React.Fragment key={step.id || i}>
                      <span className="flex shrink-0 items-center self-center text-lg text-text-tertiary">→</span>
                      <div className="flex min-w-[90px] flex-col gap-1 rounded-none border border-border bg-card px-3 py-2.5">
                        <span className="font-mono text-2xs font-bold uppercase tracking-wide text-text-tertiary">
                          {step.type.replace(/_/g, ' ')}
                        </span>
                        <span className="break-all font-mono text-2xs text-text-secondary">{step.description}</span>
                      </div>
                    </React.Fragment>
                  ))}

                  <span className="flex shrink-0 items-center self-center text-lg text-text-tertiary">→</span>
                  <div className="flex min-w-[90px] flex-col gap-1 rounded-none border border-signal/40 bg-signal/10 px-3 py-2.5">
                    <span className="font-mono text-2xs font-bold uppercase tracking-wide text-text-tertiary">Sink</span>
                    <span className="break-all font-mono text-2xs text-signal">
                      {aiPipeline.sink.type === 'file'
                        ? aiPipeline.sink.format || 'csv'
                        : aiPipeline.sink.type === 'preview'
                          ? 'Preview'
                          : aiPipeline.sink.type}
                    </span>
                  </div>
                </div>

                {/* Step detail cards */}
                <div className="flex flex-col gap-2">
                  {aiPipeline.steps.map((step, i) => (
                    <div key={step.id || i} className="flex items-start gap-3 rounded-none border border-border bg-card px-3 py-2.5">
                      <span className={STEP_BADGE}>{i + 1}</span>
                      <div>
                        <strong className="text-sm font-semibold text-card-foreground">{step.type.replace(/_/g, ' ')}</strong>
                        {step.description && <span className="text-xs text-text-tertiary"> — {step.description}</span>}
                        <div className="mt-1 flex flex-wrap gap-1.5">
                          {Object.entries(step.config).map(([k, v]) => (
                            <span key={k} className={CHIP}>
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
              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" variant="outline" size="sm" onClick={() => handleAiExecute(true)} disabled={aiExecuting}>
                  {aiExecuting ? 'Loading…' : 'Preview'}
                </Button>
                <Button type="button" size="lg" onClick={() => handleAiExecute(false)} disabled={aiExecuting}>
                  {aiExecuting ? 'Running…' : 'Execute Pipeline'}
                </Button>
                <Button type="button" variant="ghost" size="sm" onClick={handleAiSave}>
                  Save Pipeline
                </Button>
                <Button type="button" variant="ghost" size="sm" onClick={handleEditInVisualBuilder}>
                  Edit in Visual Builder
                </Button>
              </div>
            </>
          )}

          {/* ── Live pipeline run (SSE) ── */}
          {aiRunId && (
            <div className="mt-4">
              <PipelineMonitor runId={aiRunId} onComplete={handleAiRunComplete} />
            </div>
          )}

          {/* ── AI Run Result ── */}
          {aiRun && (
            <div className={RESULT_WRAP}>
              <div className={RESULT_HEADER}>
                <h3 className="text-sm font-semibold text-card-foreground">
                  {aiRun.status === 'success' ? 'Pipeline Complete' : 'Pipeline Failed'}
                </h3>
                <div className="flex flex-wrap items-center gap-3">
                  <span className={STAT}>
                    Source: <strong className="font-mono font-bold text-card-foreground">{aiRun.rows_read.toLocaleString()}</strong> rows
                  </span>
                  <span className="text-text-tertiary">→</span>
                  <span className={STAT}>
                    Output: <strong className="font-mono font-bold text-card-foreground">{aiRun.rows_written.toLocaleString()}</strong> rows
                  </span>
                  <span className={STAT_MUTED}>
                    {aiRun.steps_executed} steps · {aiRun.duration_ms.toFixed(0)}ms
                  </span>
                </div>
              </div>

              {aiRun.error && (
                <div className={cn(ERROR_BANNER, 'm-4')}>{aiRun.error}</div>
              )}

              {/* Output columns */}
              {aiRun.columns_out.length > 0 && (
                <div className="flex flex-wrap gap-1.5 px-4 py-2.5">
                  {aiRun.columns_out.map(col => (
                    <span key={col} className={CHIP_OUTPUT}>{col}</span>
                  ))}
                </div>
              )}

              {/* SQL Used */}
              {aiRun.sql_generated && (
                <details className="px-4 pb-3">
                  <summary className="cursor-pointer py-1 text-xs text-text-tertiary hover:text-text-secondary">Generated SQL</summary>
                  <pre className="mt-2 overflow-x-auto whitespace-pre rounded-none border border-border bg-card p-3 font-mono text-xs leading-relaxed text-info">{aiRun.sql_generated}</pre>
                </details>
              )}

              {/* Preview Table */}
              {aiRun.preview_data && aiRun.preview_data.length > 0 && (
                <div className={cn(TABLE_WRAP, 'mx-4 mb-3')}>
                  <table className={TABLE}>
                    <thead>
                      <tr>
                        {aiRun.columns_out.map(c => <th key={c} className={TH_RESULT}>{c}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {aiRun.preview_data.map((row, i) => (
                        <tr key={i}>
                          {aiRun.columns_out.map(c => (
                            <td key={c} className={TD}>{String(row[c] ?? '')}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Download button */}
              {aiRun.output_file && (
                <div className="px-4 pb-4">
                  <Button type="button" onClick={handleAiDownload}>
                    Download {aiRun.output_file}
                  </Button>
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
        <div className="flex flex-col gap-4">

      {/* ── Pipeline Name ── */}
      <div className="flex flex-col gap-3">
        <label className={FORM_LABEL}>Pipeline Name</label>
        <input
          className={INPUT}
          placeholder="e.g., Clean Products Data"
          value={pipelineName}
          onChange={e => setPipelineName(e.target.value)}
        />
      </div>

      {/* ── STEP 1: Source ── */}
      <div className="flex flex-col gap-3">
        <div className={SECTION_HEADER}>
          <span className={STEP_BADGE}>1</span>
          <span className={SECTION_TITLE}>Extract — Source File</span>
        </div>
        <div className="flex items-center gap-2">
          <select
            className={SELECT}
            value={selectedSource}
            onChange={e => handleSourceChange(e.target.value)}
          >
            <option value="">Select an uploaded file…</option>
            {sourceFiles.map(f => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
          <Button type="button" variant="outline" size="icon" onClick={fetchSourceFiles} title="Refresh file list">
            <RefreshCw className="size-3.5" />
          </Button>
        </div>

        {isLoading && !result && !sourcePreview && (
          <EmptyState intent="awaiting" title="Loading source preview" />
        )}

        {sourcePreview && (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-4">
              <span className={STAT}>
                <strong className="font-mono font-bold text-card-foreground">{sourcePreview.row_count.toLocaleString()}</strong> rows
              </span>
              <span className={STAT}>
                <strong className="font-mono font-bold text-card-foreground">{sourcePreview.columns.length}</strong> columns
              </span>
              <span className={STAT_MUTED}>
                table: <code className="font-mono">{sourcePreview.table_name}</code>
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {sourcePreview.columns.map(col => (
                <span key={col.name} className={CHIP} title={col.type}>
                  {col.name} <span className="text-text-tertiary">{col.type}</span>
                </span>
              ))}
            </div>
            {sourcePreview.preview.length > 0 && (
              <details className="mt-2">
                <summary className="cursor-pointer py-1.5 text-xs text-text-tertiary hover:text-text-secondary">
                  Preview first {sourcePreview.preview.length} rows
                </summary>
                <div className={TABLE_WRAP}>
                  <table className={TABLE}>
                    <thead>
                      <tr>
                        {sourcePreview.columns.map(c => <th key={c.name} className={TH}>{c.name}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {sourcePreview.preview.map((row, i) => (
                        <tr key={i}>
                          {sourcePreview.columns.map(c => (
                            <td key={c.name} className={TD}>{String(row[c.name] ?? '')}</td>
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
      <div className="flex flex-col gap-3">
        <div className={SECTION_HEADER}>
          <span className={STEP_BADGE}>2</span>
          <span className={SECTION_TITLE}>Transform — Processing Steps</span>
          <span className={STEP_COUNT}>{transforms.length} step{transforms.length !== 1 ? 's' : ''}</span>
        </div>

        {/* Transform steps list */}
        <div className="flex flex-col gap-2">
          {transforms.length === 0 ? (
            <div className="rounded-none border border-dashed border-border">
              <EmptyState
                intent="empty"
                title="No transform steps yet"
                description={'Click "Add Step" below, or use the AI Pipeline tab to generate steps automatically.'}
              />
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
          <div className="mt-2">
            {showAddStep ? (
              <div className="grid grid-cols-[repeat(auto-fill,minmax(100px,1fr))] gap-2 rounded-none border border-border bg-secondary p-3">
                {TRANSFORM_TYPES.map(t => (
                  <button
                    key={t.value}
                    type="button"
                    className={ADD_STEP_BTN}
                    onClick={() => addTransform(t.value)}
                  >
                    <span className="font-mono text-2xs font-bold text-signal">{t.icon}</span>
                    <span>{t.label}</span>
                  </button>
                ))}
                <button
                  type="button"
                  className="col-span-full flex flex-col items-center gap-1 rounded-none border border-dashed border-border bg-transparent px-2 py-2.5 font-mono text-2xs text-text-tertiary transition-colors hover:border-danger/40 hover:bg-danger/10 hover:text-danger"
                  onClick={() => setShowAddStep(false)}
                >
                  ✕ Cancel
                </button>
              </div>
            ) : (
              <button
                type="button"
                className="w-full rounded-none border border-dashed border-border bg-secondary px-4 py-2 text-center font-mono text-xs text-text-tertiary transition-colors hover:border-signal/40 hover:bg-signal/10 hover:text-signal disabled:cursor-not-allowed disabled:opacity-40"
                onClick={() => setShowAddStep(true)}
                disabled={!selectedSource}
              >
                + Add Transform Step
              </button>
            )}
          </div>
      </div>

      {/* ── STEP 3: Destination ── */}
      <div className="flex flex-col gap-3">
        <div className={SECTION_HEADER}>
          <span className={STEP_BADGE}>3</span>
          <span className={SECTION_TITLE}>Load — Destination</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex gap-2">
            {DEST_FORMATS.map(f => (
              <button
                key={f.value}
                type="button"
                className={cn(FORMAT_BASE, destFormat === f.value && FORMAT_ACTIVE)}
                onClick={() => setDestFormat(f.value)}
              >
                <span>{f.icon}</span> {f.label}
              </button>
            ))}
          </div>
          <input
            className={cn(INPUT_SM, 'font-mono')}
            placeholder="Output filename (optional)"
            value={destFilename}
            onChange={e => setDestFilename(e.target.value)}
          />
        </div>
      </div>

      {/* ── Execute ── */}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" size="sm" onClick={() => executePipeline(true)} disabled={isLoading || !selectedSource}>
          {isLoading ? 'Loading…' : 'Preview Result'}
        </Button>
        <Button type="button" size="lg" onClick={() => executePipeline(false)} disabled={isLoading || !selectedSource}>
          {isLoading ? 'Running…' : 'Execute Pipeline'}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={handleVisualSave} disabled={!selectedSource || transforms.length === 0}>
          Save Pipeline
        </Button>
      </div>

      {/* ── Result ── */}
      {result && (
        <div className={RESULT_WRAP}>
          <div className={RESULT_HEADER}>
            <h3 className="text-sm font-semibold text-card-foreground">
              {result.preview_only ? 'Preview Result' : 'Pipeline Complete'}
            </h3>
            <div className="flex flex-wrap items-center gap-3">
              <span className={STAT}>
                Source: <strong className="font-mono font-bold text-card-foreground">{result.source.row_count.toLocaleString()}</strong> rows
              </span>
              <span className="text-text-tertiary">→</span>
              <span className={STAT}>
                Output: <strong className="font-mono font-bold text-card-foreground">{result.output.row_count.toLocaleString()}</strong> rows
              </span>
              <span className={STAT_MUTED}>
                {result.transforms_applied} transform{result.transforms_applied !== 1 ? 's' : ''} · {result.execution_time_ms.toFixed(0)}ms
              </span>
            </div>
          </div>

          {/* Output columns */}
          <div className="flex flex-wrap gap-1.5 px-4 py-2.5">
            {result.output.columns.map(col => (
              <span key={col.name} className={CHIP_OUTPUT} title={col.type}>
                {col.name} <span className="text-signal/70">{col.type}</span>
              </span>
            ))}
          </div>

          {/* SQL Used */}
          {result.transform_sql && (
            <details className="px-4 pb-3">
              <summary className="cursor-pointer py-1 text-xs text-text-tertiary hover:text-text-secondary">Generated SQL</summary>
              <pre className="mt-2 overflow-x-auto whitespace-pre rounded-none border border-border bg-card p-3 font-mono text-xs leading-relaxed text-info">{result.transform_sql}</pre>
            </details>
          )}

          {/* Preview Table */}
          {result.preview.length > 0 && (
            <div className={cn(TABLE_WRAP, 'mx-4 mb-3')}>
              <table className={TABLE}>
                <thead>
                  <tr>
                    {result.output.columns.map(c => <th key={c.name} className={TH_RESULT}>{c.name}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {result.preview.map((row, i) => (
                    <tr key={i}>
                      {result.output.columns.map(c => (
                        <td key={c.name} className={TD}>{String(row[c.name] ?? '')}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Download button */}
          {!result.preview_only && result.output.file && (
            <div className="px-4 pb-4">
              <Button type="button" onClick={handleDownload}>
                Download {result.output.file}
              </Button>
            </div>
          )}
        </div>
      )}

        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════
          Saved Pipelines Tab
          ═══════════════════════════════════════════════════════════ */}
      {activeTab === 'saved' && (
        <div className="flex flex-col gap-5">
          {/* Templates Section */}
          <div className="flex flex-col gap-3">
            <div className={SECTION_HEADER}>
              <span className={STEP_BADGE}>~</span>
              <span className={SECTION_TITLE}>Quick-Start Templates</span>
              <span className={STEP_COUNT}>{PIPELINE_TEMPLATES.length} templates</span>
            </div>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">
              {PIPELINE_TEMPLATES.map(t => (
                <button
                  key={t.name}
                  type="button"
                  className={TEMPLATE_CARD}
                  onClick={() => handleUseTemplate(t)}
                >
                  <span className="self-start rounded-none border border-signal/40 bg-signal/10 px-1.5 py-0.5 font-mono text-2xs font-bold text-signal">{t.icon}</span>
                  <span className="text-sm font-semibold text-card-foreground">{t.name}</span>
                  <span className="flex-1 text-xs leading-relaxed text-text-tertiary">{t.description}</span>
                  <div className="mt-auto flex flex-wrap gap-1">
                    {t.tags.map(tag => (
                      <span key={tag} className="rounded-none border border-border bg-card px-1.5 py-0.5 font-mono text-2xs text-text-tertiary">{tag}</span>
                    ))}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Saved Pipelines List */}
          <div className="flex flex-col gap-3">
            <div className={SECTION_HEADER}>
              <span className={STEP_BADGE}>#</span>
              <span className={SECTION_TITLE}>Saved Pipelines</span>
              <span className="font-mono text-2xs text-text-tertiary">{savedPipelines.length} pipeline{savedPipelines.length !== 1 ? 's' : ''}</span>
              <Button
                type="button"
                variant="ghost"
                size="xs"
                className="ml-auto"
                onClick={fetchSavedPipelines}
                disabled={savedLoading}
                title="Refresh"
              >
                {savedLoading ? '…' : 'Refresh'}
              </Button>
            </div>

            {savedError && (
              <div className={ERROR_BANNER}>
                <span className="flex-1">⚠️ {savedError}</span>
                <button aria-label="Dismiss" title="Dismiss" onClick={() => setSavedError(null)} className="opacity-60 hover:opacity-100">
                  <span aria-hidden="true">✕</span>
                </button>
              </div>
            )}

            {savedLoading && savedPipelines.length === 0 && (
              <EmptyState intent="awaiting" title="Loading saved pipelines" />
            )}

            {!savedLoading && savedPipelines.length === 0 && !savedError && (
              <EmptyState
                intent="empty"
                title="No saved pipelines yet"
                description={'Generate a pipeline with AI or build one manually, then click "Save Pipeline" to store it here.'}
                action={<Button type="button" onClick={() => setActiveTab('ai')}>Create with AI</Button>}
              />
            )}

            {savedPipelines.length > 0 && (
              <div className="flex flex-col gap-3">
                {savedPipelines.map(p => (
                  <div key={p.id} className="rounded-none border border-border bg-secondary">
                    <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
                      <div>
                        <h4 className="text-sm font-semibold text-card-foreground">{p.name}</h4>
                        {p.description && (
                          <p className="mt-0.5 text-xs text-text-tertiary">{p.description}</p>
                        )}
                      </div>
                      <span className={cn('shrink-0 whitespace-nowrap rounded-none border px-1.5 py-0.5 font-mono text-2xs font-bold', savedStatusClass(p.status))}>
                        {p.status}
                      </span>
                    </div>
                    <div className="flex flex-wrap items-center gap-3 border-b border-border bg-card px-4 py-2 text-xs text-text-tertiary">
                      <span className="flex items-center gap-1">{p.source}</span>
                      <span className="flex items-center gap-1">{p.steps} step{p.steps !== 1 ? 's' : ''}</span>
                      <span className="flex items-center gap-1">{p.sink}</span>
                      {p.created_at && (
                        <span className="ml-auto text-text-tertiary/70">
                          {new Date(p.created_at).toLocaleDateString()}
                        </span>
                      )}
                    </div>
                    {p.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 px-4 py-2">
                        {p.tags.map(tag => (
                          <span key={tag} className="rounded-none border border-border bg-card px-1.5 py-0.5 font-mono text-2xs text-text-tertiary">{tag}</span>
                        ))}
                      </div>
                    )}
                    <div className="flex gap-2 px-4 py-3">
                      <Button type="button" size="sm" onClick={() => handleLoadPipeline(p.id)}>
                        Load & Run
                      </Button>
                      <Button
                        type="button"
                        variant="destructive"
                        size="sm"
                        onClick={() => handleDeletePipeline(p.id, p.name)}
                        disabled={deletingId === p.id}
                      >
                        {deletingId === p.id ? 'Deleting…' : 'Delete'}
                      </Button>
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
    <div className="rounded-none border border-border bg-secondary">
      <div className="flex items-center gap-2.5 border-b border-border bg-card px-3 py-2">
        <span className="flex size-5 shrink-0 items-center justify-center rounded-none border border-border bg-card font-mono text-2xs font-bold text-text-tertiary">{index + 1}</span>
        <span className="rounded-none border border-signal/40 bg-signal/10 px-1.5 py-0.5 font-mono text-2xs font-bold text-signal">{meta?.icon}</span>
        <span className="flex-1 text-sm font-medium text-card-foreground">{meta?.label || step.type}</span>
        <div className="ml-auto flex items-center gap-1">
          <Button type="button" variant="ghost" size="icon-xs" onClick={() => onMove('up')} disabled={index === 0} title="Move up">↑</Button>
          <Button type="button" variant="ghost" size="icon-xs" onClick={() => onMove('down')} disabled={index === total - 1} title="Move down">↓</Button>
          <Button type="button" variant="ghost" size="icon-xs" className="hover:text-danger" onClick={onRemove} title="Remove">✕</Button>
        </div>
      </div>

      {/* Description */}
      <input
        className={cn(INPUT_SM, 'mx-3 my-2.5 w-[calc(100%-1.5rem)]')}
        placeholder="Step description (optional)"
        value={step.description}
        onChange={e => onUpdate({ description: e.target.value })}
      />

      {/* Type-specific config */}
      <div className="flex flex-col gap-2.5 px-3 pb-3">
        {step.type === 'filter' && (
          <div className={CONFIG_FIELD}>
            <label className={FIELD_LABEL}>Condition (SQL WHERE clause)</label>
            <input
              className={INPUT_SM}
              placeholder='e.g., price > 10 AND category IS NOT NULL'
              value={step.config.condition || ''}
              onChange={e => updateConfig('condition', e.target.value)}
            />
          </div>
        )}

        {step.type === 'sort' && (
          <div className={CONFIG_ROW}>
            <div className={CONFIG_FIELD}>
              <label className={FIELD_LABEL}>Column</label>
              <select
                className={cn(SELECT, 'text-xs')}
                value={step.config.column || ''}
                onChange={e => updateConfig('column', e.target.value)}
              >
                <option value="">Select…</option>
                {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </div>
            <div className={CONFIG_FIELD}>
              <label className={FIELD_LABEL}>Order</label>
              <select
                className={cn(SELECT, 'text-xs')}
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
          <div className={CONFIG_FIELD}>
            <label className={FIELD_LABEL}>Columns to drop (comma-separated)</label>
            <input
              className={INPUT_SM}
              placeholder="col1, col2"
              value={(step.config.columns || []).join(', ')}
              onChange={e => updateConfig('columns', e.target.value.split(',').map(s => s.trim()).filter(Boolean))}
            />
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {columns.map(c => (
                <button
                  key={c.name}
                  type="button"
                  className={cn(COL_TOGGLE_BASE, (step.config.columns || []).includes(c.name) && COL_TOGGLE_ACTIVE)}
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
          <div className={CONFIG_FIELD}>
            <label className={FIELD_LABEL}>Rename mappings (one per line: old_name → new_name)</label>
            <textarea
              className={TEXTAREA_SM}
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
          <div className={CONFIG_ROW}>
            <div className={CONFIG_FIELD}>
              <label className={FIELD_LABEL}>Column Name</label>
              <input
                className={INPUT_SM}
                placeholder="new_column"
                value={step.config.name || ''}
                onChange={e => updateConfig('name', e.target.value)}
              />
            </div>
            <div className={cn(CONFIG_FIELD, 'flex-1')}>
              <label className={FIELD_LABEL}>Expression (SQL)</label>
              <input
                className={INPUT_SM}
                placeholder="e.g., price * quantity"
                value={step.config.expression || ''}
                onChange={e => updateConfig('expression', e.target.value)}
              />
            </div>
          </div>
        )}

        {step.type === 'aggregate' && (
          <>
            <div className={CONFIG_FIELD}>
              <label className={FIELD_LABEL}>Group By columns (comma-separated)</label>
              <input
                className={INPUT_SM}
                placeholder="category, region"
                value={(step.config.group_by || []).join(', ')}
                onChange={e => updateConfig('group_by', e.target.value.split(',').map(s => s.trim()).filter(Boolean))}
              />
            </div>
            <div className={CONFIG_FIELD}>
              <label className={FIELD_LABEL}>Aggregations (one per line: FUNC(column) AS alias)</label>
              <textarea
                className={TEXTAREA_SM}
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
          <div className={CONFIG_FIELD}>
            <label className={FIELD_LABEL}>Deduplicate by columns (empty = all columns)</label>
            <input
              className={INPUT_SM}
              placeholder="id, email (leave empty for full dedup)"
              value={(step.config.columns || []).join(', ')}
              onChange={e => updateConfig('columns', e.target.value.split(',').map(s => s.trim()).filter(Boolean))}
            />
          </div>
        )}

        {step.type === 'cast_type' && (
          <div className={CONFIG_ROW}>
            <div className={CONFIG_FIELD}>
              <label className={FIELD_LABEL}>Column</label>
              <select
                className={cn(SELECT, 'text-xs')}
                value={step.config.column || ''}
                onChange={e => updateConfig('column', e.target.value)}
              >
                <option value="">Select…</option>
                {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </div>
            <div className={CONFIG_FIELD}>
              <label className={FIELD_LABEL}>Target Type</label>
              <select
                className={cn(SELECT, 'text-xs')}
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
          <div className={CONFIG_ROW}>
            <div className={CONFIG_FIELD}>
              <label className={FIELD_LABEL}>Column</label>
              <select
                className={cn(SELECT, 'text-xs')}
                value={step.config.column || ''}
                onChange={e => updateConfig('column', e.target.value)}
              >
                <option value="">Select…</option>
                <option value="*">✦ All Columns</option>
                {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </div>
            <div className={CONFIG_FIELD}>
              <label className={FIELD_LABEL}>Strategy</label>
              <select
                className={cn(SELECT, 'text-xs')}
                value={step.config.strategy || 'value'}
                onChange={e => updateConfig('strategy', e.target.value)}
              >
                <option value="value">Fixed Value</option>
                <option value="mean">Mean (numeric cols)</option>
                <option value="median">Median (numeric cols)</option>
              </select>
            </div>
            <div className={cn(CONFIG_FIELD, 'flex-1')}>
              <label className={FIELD_LABEL}>{(step.config.strategy === 'mean' || step.config.strategy === 'median') ? 'Fallback for text columns' : 'Fill Value (SQL expression)'}</label>
              <input
                className={INPUT_SM}
                placeholder={step.config.column === '*' ? '0  (numeric default, text → N/A)' : "0 or 'Unknown' or AVG(col)"}
                value={step.config.value || ''}
                onChange={e => updateConfig('value', e.target.value)}
              />
            </div>
          </div>
        )}

        {step.type === 'custom_sql' && (
          <div className={CONFIG_FIELD}>
            <label className={FIELD_LABEL}>Custom SQL (use {'{{input}}'} for previous step)</label>
            <textarea
              className={TEXTAREA}
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
