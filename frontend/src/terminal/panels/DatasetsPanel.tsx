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

  if (error) return <div data-testid="datasets-panel" className="aura-panel panel-error-inline">{error}</div>;
  return (
    <div data-testid="datasets-panel" className="aura-panel datasets-panel">
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
    </div>
  );
}
