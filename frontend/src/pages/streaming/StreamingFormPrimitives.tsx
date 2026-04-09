/**
 * Reusable form primitives for the streaming pipeline create form.
 * FieldRenderer, AlertRuleBuilder, AggFieldBuilder.
 */
import React from 'react';
import { type SchemaField } from '../../services/api';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface AlertRule {
  field: string;
  operator: string;
  threshold: number;
  label: string;
}

export interface AggField {
  field: string;
  function: string;
}

// ── FieldRenderer ─────────────────────────────────────────────────────────────

interface FieldRendererProps {
  field: SchemaField;
  value: any;
  onChange: (key: string, value: any) => void;
}

export const FieldRenderer: React.FC<FieldRendererProps> = ({ field, value, onChange }) => {
  if (field.type === 'select') {
    return (
      <div className="sf-field">
        <label className="sf-label">
          {field.label}{field.required && <span className="sf-required">*</span>}
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
          {field.label}{field.required && <span className="sf-required">*</span>}
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

  return (
    <div className="sf-field">
      <label className="sf-label">
        {field.label}{field.required && <span className="sf-required">*</span>}
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

// ── AlertRuleBuilder ──────────────────────────────────────────────────────────

export const AlertRuleBuilder: React.FC<{
  rules: AlertRule[];
  onChange: (rules: AlertRule[]) => void;
}> = ({ rules, onChange }) => {
  const addRule = () => onChange([...rules, { field: '', operator: '>', threshold: 0, label: '' }]);
  const removeRule = (idx: number) => onChange(rules.filter((_, i) => i !== idx));
  const updateRule = (idx: number, key: keyof AlertRule, val: any) =>
    onChange(rules.map((r, i) => (i === idx ? { ...r, [key]: val } : r)));

  return (
    <div className="sf-alert-rules">
      <label className="sf-label">Alert Rules <span className="sf-required">*</span></label>
      {rules.map((rule, idx) => (
        <div key={idx} className="sf-alert-rule-row">
          <input className="sf-input sf-input--sm" placeholder="Field" value={rule.field}
            onChange={(e) => updateRule(idx, 'field', e.target.value)} />
          <select className="sf-input sf-select sf-input--xs" value={rule.operator}
            onChange={(e) => updateRule(idx, 'operator', e.target.value)}>
            {['>', '>=', '<', '<=', '=='].map((op) => <option key={op} value={op}>{op}</option>)}
          </select>
          <input className="sf-input sf-input--sm" type="number" step="any" placeholder="Threshold"
            value={rule.threshold} onChange={(e) => updateRule(idx, 'threshold', Number(e.target.value))} />
          <input className="sf-input sf-input--sm" placeholder="Alert label" value={rule.label}
            onChange={(e) => updateRule(idx, 'label', e.target.value)} />
          <button className="streaming-btn streaming-btn--ghost sf-btn-rm" onClick={() => removeRule(idx)} type="button">&times;</button>
        </div>
      ))}
      <button className="streaming-btn streaming-btn--primary sf-btn-add" onClick={addRule} type="button">+ Add Rule</button>
    </div>
  );
};

// ── AggFieldBuilder ───────────────────────────────────────────────────────────

export const AggFieldBuilder: React.FC<{
  fields: AggField[];
  onChange: (fields: AggField[]) => void;
}> = ({ fields, onChange }) => {
  const add = () => onChange([...fields, { field: '', function: 'COUNT' }]);
  const remove = (idx: number) => onChange(fields.filter((_, i) => i !== idx));
  const update = (idx: number, key: keyof AggField, val: string) =>
    onChange(fields.map((f, i) => (i === idx ? { ...f, [key]: val } : f)));

  return (
    <div className="sf-agg-fields">
      <label className="sf-label">Aggregations <span className="sf-required">*</span></label>
      {fields.map((agg, idx) => (
        <div key={idx} className="sf-agg-row">
          <input className="sf-input sf-input--sm" placeholder="Field name" value={agg.field}
            onChange={(e) => update(idx, 'field', e.target.value)} />
          <select className="sf-input sf-select sf-input--xs" value={agg.function}
            onChange={(e) => update(idx, 'function', e.target.value)}>
            {['SUM', 'COUNT', 'MIN', 'MAX', 'AVG'].map((fn) => <option key={fn} value={fn}>{fn}</option>)}
          </select>
          <button className="streaming-btn streaming-btn--ghost sf-btn-rm" onClick={() => remove(idx)} type="button">&times;</button>
        </div>
      ))}
      <button className="streaming-btn streaming-btn--primary sf-btn-add" onClick={add} type="button">+ Add Aggregation</button>
    </div>
  );
};
