import { useEffect, useState } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import { uploadService } from '../../services/api';
import { useCockpit } from '../CockpitProvider';

interface DatasetFile { filename: string; size: number; modified: string }

export default function DatasetsPanel(_props: IDockviewPanelProps) {
  const { activeDataset, setActiveDataset } = useCockpit();
  const [files, setFiles] = useState<DatasetFile[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    uploadService.getUploadedFiles()
      .then((f) => { if (alive) setFiles(f); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : 'Failed to load datasets'); });
    return () => { alive = false; };
  }, []);

  return (
    <div data-testid="datasets-panel" className="aura-panel datasets-panel">
      <div className="panel-head">
        <span className="panel-head-glyph" aria-hidden>▤</span>
        <span className="panel-head-title">Datasets</span>
        <span className="panel-head-metric">
          {error ? 'error' : `${files.length} loaded`}
        </span>
      </div>
      {error ? (
        <div className="panel-empty is-error" role="alert">
          <span className="panel-empty-glyph" aria-hidden>●</span>
          <span className="panel-empty-title">Couldn't load datasets</span>
          <span className="panel-empty-hint">{error}</span>
        </div>
      ) : files.length === 0 ? (
        <div className="panel-empty is-idle" role="status">
          <span className="panel-empty-glyph" aria-hidden>·</span>
          <span className="panel-empty-title">No datasets</span>
          <span className="panel-empty-hint">Upload a file to make it selectable here.</span>
        </div>
      ) : (
        <table className="datasets-table">
          <thead><tr><th>Dataset</th><th>Size</th></tr></thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.filename}
                  data-testid={`dataset-row-${f.filename}`}
                  className={f.filename === activeDataset ? 'is-active' : ''}
                  onClick={() => setActiveDataset(f.filename)}>
                <td>{f.filename}</td><td>{f.size}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
