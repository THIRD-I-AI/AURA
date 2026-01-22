import { useState } from 'react';
import './PipelinesPanel.css';

function PipelinesPanel() {
  const [jobs] = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);

  return (
    <div className="pipelines-panel">
      <div className="pipelines-header">
        <h2>🚀 Automated Pipelines</h2>
        <p>Schedule and automate your data workflows</p>
      </div>

      <div className="pipelines-content">
        <div className="jobs-list">
          {jobs.length === 0 ? (
            <p style={{ padding: '2rem', textAlign: 'center', color: '#888' }}>
              No pipelines yet. Create one to get started.
            </p>
          ) : (
            jobs.map((job: any) => (
              <div key={job.id} className="job-item" onClick={() => setSelectedJob(job)}>
                {job.name}
              </div>
            ))
          )}
        </div>

        <div className="job-details">
          {selectedJob ? (
            <div>
              <h3>{(selectedJob as any).name}</h3>
              <p>Selected job details</p>
            </div>
          ) : (
            <div style={{ padding: '2rem', textAlign: 'center', color: '#999' }}>
              Select a pipeline to view details
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default PipelinesPanel;
