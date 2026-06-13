import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { auditApi } from './auditApi';
import { useCsvPreview } from './useCsvPreview';
import { validateMapping } from './validateMapping';
import { mappingTypeGuard } from './mappingTypeGuard';
import type { ColumnMapping } from './types';
import { UploadStep } from './wizard/UploadStep';
import { MapStep } from './wizard/MapStep';
import { ReviewStep } from './wizard/ReviewStep';
import { Stepper } from '../ui/Stepper';
import { Button } from '../ui/Button';

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
  const guard = useMemo(
    () => mappingTypeGuard(mapping, preview.columns, preview.types, preview.previewRows),
    [mapping, preview.columns, preview.types, preview.previewRows],
  );
  // Blocking errors merge with validateMapping (required/collision takes priority);
  // the backend auto-encodes the rest, surfaced as non-blocking notes.
  const mapErrors = {
    treatment: validation.errors.treatment ?? guard.errors.treatment,
    outcome: validation.errors.outcome ?? guard.errors.outcome,
    confounders: validation.errors.confounders ?? guard.errors.confounders,
    instrument: validation.errors.instrument ?? guard.errors.instrument,
  };
  const mappingOk = validation.valid && Object.keys(guard.errors).length === 0;

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

  return (
    <div data-testid="audit-wizard" className="aud-wizard">
      <h2>Audit your own data</h2>
      <div className="aud-wizard__steps">
        <Stepper steps={['Upload', 'Map', 'Review']} current={step} />
        {/* Legacy testid hooks retained for existing assertions. */}
        <div hidden>
          {['Upload', 'Map', 'Review'].map((label, i) => (
            <span key={label} data-testid={`wizard-dot-${i}`}>{i + 1}. {label}</span>
          ))}
        </div>
      </div>

      {step === 0 && <UploadStep file={file} columns={preview.columns} previewRows={preview.previewRows} types={preview.types} uploading={uploading} error={uploadError} onPick={pickFile} />}
      {step === 1 && <MapStep columns={preview.columns} mapping={mapping} errors={mapErrors} notes={guard.notes} onChange={setMapping} />}
      {step === 2 && <ReviewStep filename={filename} mapping={mapping} />}

      {runError && <p className="aud-wizard__err">{runError}</p>}

      <div className="aud-wizard__nav">
        {step > 0
          ? <Button data-testid="wizard-back" variant="secondary" onClick={() => setStep((s) => s - 1)}>Back</Button>
          : <span />}
        {step < 2
          ? <Button data-testid="wizard-next" disabled={!canNext} onClick={() => setStep((s) => s + 1)}>Next</Button>
          : <Button data-testid="wizard-run" disabled={running || !mappingOk || filename === null} onClick={run}>{running ? 'Running…' : 'Run audit'}</Button>}
      </div>
    </div>
  );
}
