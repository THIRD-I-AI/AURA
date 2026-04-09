import React from 'react';
import { type StreamingSchemas } from '../../services/api';
import { FieldRenderer, AlertRuleBuilder, AggFieldBuilder } from './StreamingFormPrimitives';

interface SinkEntry   { type: string; config: Record<string, any> }
interface TransformEntry { type: string; description: string; config: Record<string, any> }

interface Props {
  schemas: StreamingSchemas;
  isLoading: boolean;
  // form state
  formName: string;           setFormName: (v: string) => void;
  formDesc: string;           setFormDesc: (v: string) => void;
  formSourceType: string;
  formSourceConfig: Record<string, any>; setFormSourceConfig: (v: Record<string, any>) => void;
  formWindowType: string;
  formWindowConfig: Record<string, any>; setFormWindowConfig: (v: Record<string, any>) => void;
  formEventTimeField: string; setFormEventTimeField: (v: string) => void;
  formWatermarkDelay: number; setFormWatermarkDelay: (v: number) => void;
  formCheckpointInterval: number; setFormCheckpointInterval: (v: number) => void;
  formSinks: SinkEntry[];
  formTransforms: TransformEntry[];
  // handlers
  onSourceTypeChange: (t: string) => void;
  onWindowTypeChange: (t: string) => void;
  onAddSink: (t: string) => void;
  onRemoveSink: (idx: number) => void;
  onSinkConfigChange: (idx: number, key: string, value: any) => void;
  onAddTransform: (t: string) => void;
  onRemoveTransform: (idx: number) => void;
  onTransformConfigChange: (idx: number, key: string, value: any) => void;
  onSubmit: () => void;
  onCancel: () => void;
}

const StreamingCreateForm: React.FC<Props> = (p) => {
  const currentSourceSchema = p.schemas.sources[p.formSourceType];
  const currentWindowSchema = p.schemas.windows[p.formWindowType];

  return (
    <div className="streaming-create-form">
      <h2>Create Streaming Pipeline</h2>
      <p className="sf-subtitle">Configure a real-time data pipeline with your own sources, sinks, and processing logic.</p>

      {/* ── Pipeline Info ── */}
      <fieldset className="sf-section">
        <legend>Pipeline Info</legend>
        <div className="sf-row">
          <div className="sf-field sf-field--wide">
            <label className="sf-label">Pipeline Name <span className="sf-required">*</span></label>
            <input className="sf-input" type="text" placeholder="e.g. Sales Order Monitor"
              value={p.formName} onChange={(e) => p.setFormName(e.target.value)} />
          </div>
          <div className="sf-field sf-field--wide">
            <label className="sf-label">Description</label>
            <input className="sf-input" type="text" placeholder="What does this pipeline do?"
              value={p.formDesc} onChange={(e) => p.setFormDesc(e.target.value)} />
          </div>
        </div>
      </fieldset>

      {/* ── Data Source ── */}
      <fieldset className="sf-section">
        <legend>Data Source</legend>
        <div className="sf-field">
          <label className="sf-label">Source Type <span className="sf-required">*</span></label>
          <div className="sf-source-cards">
            {Object.entries(p.schemas.sources).map(([key, src]) => (
              <button key={key} type="button"
                className={`sf-source-card ${p.formSourceType === key ? 'sf-source-card--selected' : ''} ${!src.implemented ? 'sf-source-card--disabled' : ''}`}
                onClick={() => src.implemented && p.onSourceTypeChange(key)}
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
              <FieldRenderer key={field.key} field={field}
                value={p.formSourceConfig[field.key]}
                onChange={(k, v) => p.setFormSourceConfig({ ...p.formSourceConfig, [k]: v })} />
            ))}
          </div>
        )}
        <div className="sf-row">
          <div className="sf-field">
            <label className="sf-label">Event Time Field</label>
            <input className="sf-input" type="text" value={p.formEventTimeField}
              onChange={(e) => p.setFormEventTimeField(e.target.value)} />
            <span className="sf-help">Field in event data that holds the timestamp</span>
          </div>
          <div className="sf-field">
            <label className="sf-label">Watermark Delay (s)</label>
            <input className="sf-input" type="number" value={p.formWatermarkDelay}
              onChange={(e) => p.setFormWatermarkDelay(Number(e.target.value))} />
            <span className="sf-help">How long to wait for late events before advancing the watermark</span>
          </div>
        </div>
      </fieldset>

      {/* ── Window Strategy ── */}
      <fieldset className="sf-section">
        <legend>Window Strategy</legend>
        <div className="sf-field">
          <label className="sf-label">Window Type</label>
          <div className="sf-window-cards">
            {Object.entries(p.schemas.windows).map(([key, win]) => (
              <button key={key} type="button"
                className={`sf-window-card ${p.formWindowType === key ? 'sf-window-card--selected' : ''}`}
                onClick={() => p.onWindowTypeChange(key)}
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
              <FieldRenderer key={field.key} field={field}
                value={p.formWindowConfig[field.key]}
                onChange={(k, v) => p.setFormWindowConfig({ ...p.formWindowConfig, [k]: v })} />
            ))}
          </div>
        )}
      </fieldset>

      {/* ── Transforms ── */}
      <fieldset className="sf-section">
        <legend>Transforms (optional)</legend>
        <p className="sf-help-block">Add processing steps that run on each event before windowing.</p>
        {p.formTransforms.map((t, idx) => {
          const tSchema = p.schemas.transforms[t.type];
          return (
            <div key={idx} className="sf-transform-block">
              <div className="sf-transform-header">
                <strong>{tSchema?.label || t.type}</strong>
                <button className="streaming-btn streaming-btn--ghost sf-btn-rm"
                  onClick={() => p.onRemoveTransform(idx)} type="button">&times;</button>
              </div>
              <div className="sf-row">
                {tSchema?.fields.map((field) => {
                  if (field.type === 'agg_fields') {
                    return (
                      <AggFieldBuilder key={field.key}
                        fields={t.config[field.key] || []}
                        onChange={(aggs) => p.onTransformConfigChange(idx, field.key, aggs)} />
                    );
                  }
                  return (
                    <FieldRenderer key={field.key} field={field}
                      value={t.config[field.key]}
                      onChange={(k, v) => p.onTransformConfigChange(idx, k, v)} />
                  );
                })}
              </div>
            </div>
          );
        })}
        <div className="sf-add-btns">
          {Object.entries(p.schemas.transforms).map(([key, ts]) => (
            <button key={key} type="button"
              className="streaming-btn streaming-btn--primary sf-btn-add"
              onClick={() => p.onAddTransform(key)}>
              + {ts.label}
            </button>
          ))}
        </div>
      </fieldset>

      {/* ── Sinks ── */}
      <fieldset className="sf-section">
        <legend>Output Sinks</legend>
        <p className="sf-help-block">Choose where processed window results are sent. SSE (live to UI) is always included.</p>
        {p.formSinks.map((s, idx) => {
          const sinkSchema = p.schemas.sinks[s.type];
          return (
            <div key={idx} className="sf-sink-block">
              <div className="sf-sink-header">
                <strong>{sinkSchema?.label || s.type}</strong>
                <button className="streaming-btn streaming-btn--ghost sf-btn-rm"
                  onClick={() => p.onRemoveSink(idx)} type="button">&times;</button>
              </div>
              <div className="sf-row">
                {sinkSchema?.fields.map((field) => {
                  if (field.type === 'alert_rules') {
                    return (
                      <AlertRuleBuilder key={field.key}
                        rules={s.config[field.key] || []}
                        onChange={(rules) => p.onSinkConfigChange(idx, field.key, rules)} />
                    );
                  }
                  return (
                    <FieldRenderer key={field.key} field={field}
                      value={s.config[field.key]}
                      onChange={(k, v) => p.onSinkConfigChange(idx, k, v)} />
                  );
                })}
              </div>
            </div>
          );
        })}
        <div className="sf-add-btns">
          {Object.entries(p.schemas.sinks).filter(([, s]) => s.implemented).map(([key, sink]) => (
            <button key={key} type="button"
              className={`streaming-btn sf-btn-add ${p.formSinks.some((s) => s.type === key) ? 'streaming-btn--ghost' : 'streaming-btn--primary'}`}
              onClick={() => p.onAddSink(key)}
              disabled={p.formSinks.some((s) => s.type === key)}
            >
              {p.formSinks.some((s) => s.type === key) ? `✓ ${sink.label}` : `+ ${sink.label}`}
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
            <input className="sf-input" type="number" value={p.formCheckpointInterval}
              onChange={(e) => p.setFormCheckpointInterval(Number(e.target.value))} />
            <span className="sf-help">How often to save pipeline state for recovery</span>
          </div>
        </div>
      </fieldset>

      {/* ── Submit ── */}
      <div className="sf-submit-row">
        <button className="streaming-btn streaming-btn--primary sf-submit-btn"
          onClick={p.onSubmit} disabled={p.isLoading || !p.formName.trim()}>
          {p.isLoading ? 'Creating…' : 'Create Pipeline'}
        </button>
        <button className="streaming-btn streaming-btn--ghost" onClick={p.onCancel} type="button">Cancel</button>
      </div>
    </div>
  );
};

export default StreamingCreateForm;
