import React, { useState, useEffect, useCallback } from 'react';
import { type PageType } from '../components/Layout/AppLayout';
import {
  etlService,
  pipelineService,
  type ETLColumnSchema,
  type ETLTransformStep,
  type ETLSourcePreview,
  type ETLExecutionResult,
  type PipelineDef,
  type PipelineRunResult,
} from '../services/api';
import './PipelinesPanel.css';

/* ================================================================
   Types
   ================================================================ */

type TransformType = ETLTransformStep['type'];

const TRANSFORM_TYPES: { value: TransformType; label: string; icon: string }[] = [
  { value: 'filter', label: 'Filter Rows', icon: '🔍' },
  { value: 'sort', label: 'Sort', icon: '↕️' },
  { value: 'drop_columns', label: 'Drop Columns', icon: '✂️' },
  { value: 'rename', label: 'Rename Columns', icon: '✏️' },
  { value: 'add_column', label: 'Add Column', icon: '➕' },
  { value: 'aggregate', label: 'Aggregate', icon: '📊' },
  { value: 'deduplicate', label: 'Deduplicate', icon: '🧹' },
  { value: 'cast_type', label: 'Cast Type', icon: '🔄' },
  { value: 'fill_missing', label: 'Fill Missing', icon: '🩹' },
  { value: 'custom_sql', label: 'Custom SQL', icon: '⚡' },
];

const DEST_FORMATS = [
  { value: 'csv', label: 'CSV', icon: '📄' },
  { value: 'parquet', label: 'Parquet', icon: '🗂️' },
  { value: 'json', label: 'JSON', icon: '{ }' },
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
  const [activeTab, setActiveTab] = useState<'visual' | 'ai'>('ai');
  const [showAddStep, setShowAddStep] = useState(false);

  // ── AI Pipeline State ──
  const [aiPrompt, setAiPrompt] = useState('');
  const [aiGenerating, setAiGenerating] = useState(false);
  const [aiPipeline, setAiPipeline] = useState<PipelineDef | null>(null);
  const [aiRun, setAiRun] = useState<PipelineRunResult | null>(null);
  const [aiExecuting, setAiExecuting] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);

  // ── Fetch available uploaded files ──
  useEffect(() => {
    fetchSourceFiles();
  }, []);

  const fetchSourceFiles = async () => {
    try {
      const resp = await fetch(`${localStorage.getItem('apiUrl') || 'http://localhost:8000'}/files`);
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

  // ── AI Pipeline: Execute ──
  const handleAiExecute = async (previewOnly: boolean) => {
    if (!aiPipeline) return;
    setAiExecuting(true);
    setAiError(null);
    setAiRun(null);
    console.log('[AI Pipeline] Executing (preview=%s):', previewOnly, aiPipeline.name);
    try {
      const resp = await pipelineService.execute(aiPipeline, previewOnly);
      console.log('[AI Pipeline] Execute response:', resp);
      if (resp.status === 'success' && resp.run) {
        setAiRun(resp.run);
      } else {
        setAiError(resp.error || 'Pipeline execution failed');
      }
    } catch (e: any) {
      setAiError(e.message || 'Pipeline execution failed');
    } finally {
      setAiExecuting(false);
    }
  };

  // ── AI Pipeline: Save ──
  const handleAiSave = async () => {
    if (!aiPipeline) return;
    try {
      const resp = await pipelineService.save(aiPipeline);
      if (resp.status === 'success') {
        setAiError(null);
        alert(`Pipeline saved: ${resp.name} (${resp.pipeline_id})`);
      }
    } catch (e: any) {
      setAiError(e.message || 'Failed to save pipeline');
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
      {/* ── Header ── */}
      <div className="etl-header">
        <div className="etl-header__left">
          <span className="etl-header__icon">⚙️</span>
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
            🤖 AI Pipeline
          </button>
          <button
            className={`etl-tab ${activeTab === 'visual' ? 'etl-tab--active' : ''}`}
            onClick={() => setActiveTab('visual')}
          >
            🧩 Visual Builder
          </button>
        </div>
      </div>

      {error && activeTab !== 'ai' && (
        <div className="etl-error">
          ⚠️ {error}
          <button className="etl-error__close" onClick={() => setError(null)}>✕</button>
        </div>
      )}

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
                >
                  <option value="">Auto-detect source file</option>
                  {sourceFiles.map(f => <option key={f} value={f}>{f}</option>)}
                </select>
              )}
              <button
                className="etl-btn etl-btn--primary"
                onClick={handleAiGenerate}
                disabled={aiGenerating || !aiPrompt.trim()}
              >
                {aiGenerating ? '⏳ Generating…' : '🤖 Generate Pipeline'}
              </button>
            </div>
          </div>

          {aiError && (
            <div className="etl-error">
              ⚠️ {aiError}
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
                      ⚡ Local
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
                        ? `📄 ${aiPipeline.source.file_name || 'file'}`
                        : `🗄️ ${aiPipeline.source.type}://${aiPipeline.source.table || 'query'}`}
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
                        ? `📄 ${aiPipeline.sink.format || 'csv'}`
                        : aiPipeline.sink.type === 'preview'
                          ? '👁️ Preview'
                          : `🗄️ ${aiPipeline.sink.type}`}
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
                  {aiExecuting ? '⏳' : '👁️'} Preview
                </button>
                <button
                  className="etl-btn etl-btn--primary etl-btn--lg"
                  onClick={() => handleAiExecute(false)}
                  disabled={aiExecuting}
                >
                  {aiExecuting ? '⏳ Running…' : '▶ Execute Pipeline'}
                </button>
                <button
                  className="etl-btn etl-btn--ghost"
                  onClick={handleAiSave}
                >
                  💾 Save Pipeline
                </button>
                <button
                  className="etl-btn etl-btn--ghost"
                  onClick={handleEditInVisualBuilder}
                >
                  🧩 Edit in Visual Builder
                </button>
              </div>
            </>
          )}

          {/* ── AI Run Result ── */}
          {aiRun && (
            <div className="etl-result">
              <div className="etl-result__header">
                <h3>
                  {aiRun.status === 'success' ? '✅ Pipeline Complete' : '❌ Pipeline Failed'}
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
                <div className="etl-error">⚠️ {aiRun.error}</div>
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
                    ⬇️ Download {aiRun.output_file}
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
      {activeTab !== 'ai' && (
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
            🔄
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
          {isLoading ? '⏳' : '👁️'} Preview Result
        </button>
        <button
          className="etl-btn etl-btn--primary etl-btn--lg"
          onClick={() => executePipeline(false)}
          disabled={isLoading || !selectedSource}
        >
          {isLoading ? '⏳ Running…' : '▶ Execute Pipeline'}
        </button>
      </div>

      {/* ── Result ── */}
      {result && (
        <div className="etl-result">
          <div className="etl-result__header">
            <h3>
              {result.preview_only ? '👁️ Preview Result' : '✅ Pipeline Complete'}
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
                ⬇️ Download {result.output.file}
              </button>
            </div>
          )}
        </div>
      )}

        </>
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
            <div className="etl-config-field etl-config-field--grow">
              <label>Fill Value (SQL expression)</label>
              <input
                className="etl-input"
                placeholder="0 or 'Unknown' or AVG(col)"
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
    case 'fill_missing': return { column: '', value: '' };
    case 'custom_sql': return { sql: '' };
    default: return {};
  }
}

export default PipelinesPanel;
