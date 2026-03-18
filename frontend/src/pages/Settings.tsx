import React, { useState, useEffect } from 'react';
import { type PageType } from '../components/Layout/AppLayout';
import { healthService, type HealthStatus } from '../services/api';
import './Settings.css';

interface SettingsProps {
  setCurrentPage?: (page: PageType) => void;
}

const Settings: React.FC<SettingsProps> = () => {
  const [apiUrl, setApiUrl] = useState('http://localhost:8000');
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem('apiUrl');
    if (stored) setApiUrl(stored);
  }, []);

  const handleTestConnection = async () => {
    setChecking(true);
    try {
      const result = await healthService.checkHealth();
      setHealth(result);
    } catch {
      setHealth({ status: 'down', timestamp: new Date().toISOString() });
    } finally {
      setChecking(false);
    }
  };

  const handleSave = () => {
    localStorage.setItem('apiUrl', apiUrl);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleClearData = () => {
    if (window.confirm('Clear all local data? This removes uploaded files and query history from the browser.')) {
      localStorage.removeItem('recentUploads');
      localStorage.removeItem('queryHistory');
      localStorage.removeItem('active_dataset');
      window.location.reload();
    }
  };

  return (
    <div className="settings-page">
      <div className="settings-container">

        <header className="settings-header">
          <h1 className="settings-title">Settings</h1>
          <p className="settings-subtitle">Configure connections, preferences, and system options.</p>
        </header>

        <div className="settings-grid">

          {/* API Connection */}
          <section className="settings-section">
            <h2 className="settings-section-title">🔗 API Connection</h2>
            <p className="settings-section-desc">Backend endpoint for all data operations.</p>

            <div className="settings-field">
              <label className="settings-label" htmlFor="api-url">Backend URL</label>
              <div className="settings-input-row">
                <input
                  id="api-url"
                  type="text"
                  value={apiUrl}
                  onChange={(e) => setApiUrl(e.target.value)}
                  className="settings-input"
                  placeholder="http://localhost:8000"
                />
                <button
                  className="settings-btn-secondary"
                  onClick={handleTestConnection}
                  disabled={checking}
                >
                  {checking ? 'Testing…' : 'Test'}
                </button>
              </div>
              {health && (
                <div className={`settings-health-badge ${health.status}`}>
                  {health.status === 'healthy' && '✅ Connected — backend is healthy'}
                  {health.status === 'degraded' && '⚠️ Degraded — some services have issues'}
                  {health.status === 'down' && '❌ Offline — cannot reach backend'}
                </div>
              )}
            </div>

            <div className="settings-actions">
              <button className="settings-btn-primary" onClick={handleSave}>
                {saved ? '✓ Saved' : 'Save Changes'}
              </button>
            </div>
          </section>

          {/* Appearance */}
          <section className="settings-section">
            <h2 className="settings-section-title">🎨 Appearance</h2>
            <p className="settings-section-desc">Theme and display preferences.</p>

            <div className="settings-option-row">
              <div>
                <p className="settings-option-label">Dark mode</p>
                <p className="settings-option-hint">System follows your OS preference by default.</p>
              </div>
              <span className="settings-option-value">
                {window.matchMedia('(prefers-color-scheme: dark)').matches ? 'Active' : 'Light'}
              </span>
            </div>

            <div className="settings-option-row">
              <div>
                <p className="settings-option-label">Compact tables</p>
                <p className="settings-option-hint">Reduce row padding in data tables.</p>
              </div>
              <span className="settings-option-value">Off</span>
            </div>
          </section>

          {/* System Info */}
          <section className="settings-section">
            <h2 className="settings-section-title">ℹ️ System</h2>
            <p className="settings-section-desc">Version and diagnostics.</p>

            <div className="settings-info-grid">
              <div className="settings-info-item">
                <span className="settings-info-label">App</span>
                <span className="settings-info-value">AURA Analytics v1.0</span>
              </div>
              <div className="settings-info-item">
                <span className="settings-info-label">Frontend</span>
                <span className="settings-info-value">React + Vite</span>
              </div>
              <div className="settings-info-item">
                <span className="settings-info-label">Backend</span>
                <span className="settings-info-value">FastAPI</span>
              </div>
              <div className="settings-info-item">
                <span className="settings-info-label">AI Model</span>
                <span className="settings-info-value">Gemini (NL → SQL)</span>
              </div>
            </div>
          </section>

          {/* Danger Zone */}
          <section className="settings-section settings-danger">
            <h2 className="settings-section-title">⚠️ Danger Zone</h2>
            <p className="settings-section-desc">Irreversible actions.</p>

            <div className="settings-option-row">
              <div>
                <p className="settings-option-label">Clear all local data</p>
                <p className="settings-option-hint">Removes uploaded file references and query history from browser storage.</p>
              </div>
              <button className="settings-btn-danger" onClick={handleClearData}>
                Clear Data
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
};

export default Settings;
