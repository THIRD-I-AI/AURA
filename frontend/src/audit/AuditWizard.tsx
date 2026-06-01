import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { auditApi } from './auditApi';
import { useCsvPreview } from './useCsvPreview';
import { validateMapping } from './validateMapping';
import { nonNumericMappingErrors } from './mappingTypeGuard';
import type { ColumnMapping } from './types';
import { UploadStep } from './wizard/UploadStep';
import { MapStep } from './wizard/MapStep';
import { ReviewStep } from './wizard/ReviewStep';

const EMPTY_MAPPING: ColumnMapping = { treatment: '', outcome: '', confounders: [] };

export function AuditWizard() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [mapping, setMapping] = useState<ColumnMapping>(EMPTY_MAPPING);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const preview = useCsvPreview(file);
  const validation = useMemo(() => validateMapping(mapping, preview.columns), [mapping, preview.columns]);
  const typeErrs = useMemo(() => nonNumericMappingErrors(mapping, preview.types), [mapping, preview.types]);
  // Surface the numeric guard per role without masking a higher-priority
  // required/collision error from validateMapping.
  const mapErrors = {
    treatment: validation.errors.treatment ?? typeErrs.treatment,
    outcome: validation.errors.outcome ?? typeErrs.outcome,
    confounders: validation.errors.confounders ?? typeErrs.confounders,
    instrument: validation.errors.instrument ?? typeErrs.instrument,
  };
  const mappingOk = validation.valid && Object.keys(typeErrs).length === 0;

  const pickFile = async (f: File) => {
    setFile(f);
    setMapping(EMPTY_MAPPING);
    setUploadError(null);
    setUploading(true);
    try {
      const { filename: name } = await auditApi.uploadDataset(f);
      setFilename(name);
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  const run = async () => {
    if (!filename) return;
    setRunning(true);
    setRunError(null);
    try {
      const { job_id } = await auditApi.runDataAudit({
        uploaded_file: filename,
        treatment: mapping.treatment,
        outcome: mapping.outcome,
        confounders: mapping.confounders,
        instrument: mapping.instrument,
      });
      navigate(`/audit/${job_id}`);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
      setRunning(false);
    }
  };

  const canNext = step === 0
    ? preview.columns.length > 0 && !uploading && !uploadError && filename !== null
    : step === 1
      ? mappingOk
      : false;

  const btn = (testid: string, label: string, enabled: boolean, onClick: () => void) => (
    <button data-testid={testid} disabled={!enabled} onClick={onClick}
      style={{ padding: 'var(--space-3) var(--space-6)', background: enabled ? 'var(--accent)' : 'var(--border-default)', color: '#fff', border: 'none', borderRadius: 'var(--radius-md)', cursor: enabled ? 'pointer' : 'not-allowed' }}>
      {label}
    </button>
  );

  return (
    <div data-testid="audit-wizard" style={{ maxWidth: 640, margin: '0 auto' }}>
      <h2>Audit your own data</h2>
      <div style={{ display: 'flex', gap: 'var(--space-2)', margin: 'var(--space-3) 0 var(--space-6)' }}>
        {['Upload', 'Map', 'Review'].map((label, i) => (
          <span key={label} data-testid={`wizard-dot-${i}`} style={{ fontSize: 'var(--font-xs)', textTransform: 'uppercase', letterSpacing: '0.06em',
            color: i === step ? 'var(--accent)' : i < step ? 'var(--green)' : 'var(--text-tertiary)' }}>
            {i + 1}. {label}
          </span>
        ))}
      </div>

      {step === 0 && <UploadStep file={file} columns={preview.columns} previewRows={preview.previewRows} types={preview.types} uploading={uploading} error={uploadError} onPick={pickFile} />}
      {step === 1 && <MapStep columns={preview.columns} mapping={mapping} errors={mapErrors} onChange={setMapping} />}
      {step === 2 && <ReviewStep filename={filename} mapping={mapping} />}

      {runError && <p style={{ color: 'var(--red)' }}>{runError}</p>}

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 'var(--space-6)' }}>
        {step > 0
          ? btn('wizard-back', 'Back', true, () => setStep((s) => s - 1))
          : <span />}
        {step < 2
          ? btn('wizard-next', 'Next', canNext, () => setStep((s) => s + 1))
          : btn('wizard-run', running ? 'Running…' : 'Run audit', !running && mappingOk && filename !== null, run)}
      </div>
    </div>
  );
}
