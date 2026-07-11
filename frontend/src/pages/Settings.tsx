import React, { useState, useEffect } from 'react';
import { type PageType } from '../components/Layout/AppLayout';
import { healthService, type HealthStatus } from '../services/api';
import ConnectorCatalog from '../components/ConnectorCatalog';
import './Settings.css';

interface SettingsProps {
  setCurrentPage?: (page: PageType) => void;
}

// ── SVG Icons ─────────────────────────────────────────────────────────────────

const LinkIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
  </svg>
);

const PaletteIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/>
    <circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/>
    <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/>
  </svg>
);

const InfoIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10"/>
    <line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
  </svg>
);

const AlertTriangleIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
    <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
  </svg>
);

const MoonIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
  </svg>
);

const SunIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <circle cx="12" cy="12" r="5"/>
    <line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/>
    <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
    <line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>
    <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
  </svg>
);

const CheckIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#34d399" strokeWidth="2.5" strokeLinecap="round">
    <polyline points="20 6 9 17 4 12"/>
  </svg>
);

const XIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#f87171" strokeWidth="2.5" strokeLinecap="round">
    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
  </svg>
);

const WarnIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fbbf24" strokeWidth="2.5" strokeLinecap="round">
    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
    <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
  </svg>
);

// ─────────────────────────────────────────────────────────────────────────────

const SectionHeader: React.FC<{
  icon: React.ReactNode;
  title: string;
  desc: string;
}> = ({ icon, title, desc }) => (
  <div className="settings-section__header">
    <h2 className="settings-section__title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {icon} {title}
    </h2>
    <p className="settings-section__desc">{desc}</p>
  </div>
);

// ─────────────────────────────────────────────────────────────────────────────

const Settings: React.FC<SettingsProps> = () => {
  // Empty = same-origin (production default). The placeholder shows the dev URL.
  const [apiUrl, setApiUrl] = useState((import.meta.env.VITE_API_URL as string) || '');
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [saved, setSaved] = useState(false);
  const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

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

  const serviceEntries = health?.services ? Object.entries(health.services) : [];

  return (
    <div className="settings-page">

      {/* ── API Connection ──────────────────────────────────────── */}
      <section className="settings-section">
        <SectionHeader
          icon={<LinkIcon />}
          title="API Connection"
          desc="Backend endpoint for all data operations."
        />

        {/* URL + test */}
        <div className="settings-row">
          <div>
            <p className="settings-row__label">Backend URL</p>
            <p className="settings-row__hint">The FastAPI server AURA connects to.</p>
          </div>
          <div className="settings-row__control" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              type="text"
              value={apiUrl}
              onChange={e => setApiUrl(e.target.value)}
              style={{
                background: 'var(--bg-sunken)', border: '1px solid var(--border-default)',
                borderRadius: 'var(--radius-md)', color: 'var(--text-primary)',
                fontSize: 'var(--font-sm)', padding: '6px 10px', fontFamily: 'var(--font-mono)',
                width: 220, outline: 'none',
              }}
              placeholder="http://localhost:8000"
            />
            <button
              onClick={handleTestConnection}
              disabled={checking}
              style={{
                padding: '6px 14px', background: 'var(--bg-elevated)',
                border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)',
                color: checking ? 'var(--text-disabled)' : 'var(--text-secondary)',
                fontSize: 'var(--font-sm)', fontWeight: 600, cursor: checking ? 'not-allowed' : 'pointer',
                fontFamily: 'var(--font-sans)',
              }}
            >
              {checking ? 'Testing…' : 'Test'}
            </button>
          </div>
        </div>

        {/* Health result */}
        {health && (
          <>
            <div className="settings-row" style={{ borderBottom: serviceEntries.length > 0 ? '1px solid var(--border-hairline)' : 'none' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {health.status === 'healthy'  && <CheckIcon />}
                {health.status === 'degraded' && <WarnIcon />}
                {health.status === 'down'     && <XIcon />}
                <span style={{ fontSize: 'var(--font-sm)', color: health.status === 'healthy' ? '#34d399' : health.status === 'degraded' ? '#fbbf24' : '#f87171', fontWeight: 500 }}>
                  {health.status === 'healthy'  && 'Connected — backend is healthy'}
                  {health.status === 'degraded' && 'Degraded — some services have issues'}
                  {health.status === 'down'     && 'Offline — cannot reach backend'}
                </span>
              </div>
              <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-disabled)', fontFamily: 'var(--font-mono)' }}>
                {new Date(health.timestamp).toLocaleTimeString()}
              </span>
            </div>

            {serviceEntries.length > 0 && (
              <div className="health-grid">
                {serviceEntries.map(([name, ok]) => (
                  <div key={name} className="health-item">
                    <span className="health-item__name">{name}</span>
                    <span className={`health-badge health-badge--${ok ? 'healthy' : 'down'}`}>
                      {ok ? 'OK' : 'Down'}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {/* Save */}
        <div className="settings-row" style={{ borderBottom: 'none' }}>
          <div>
            <p className="settings-row__label">Save configuration</p>
            <p className="settings-row__hint">Persists the URL to localStorage.</p>
          </div>
          <div className="settings-row__control">
            <button
              onClick={handleSave}
              style={{
                padding: '6px 18px', background: saved ? '#166534' : 'var(--accent)',
                border: 'none', borderRadius: 'var(--radius-md)',
                color: '#fff', fontSize: 'var(--font-sm)', fontWeight: 600,
                cursor: 'pointer', fontFamily: 'var(--font-sans)',
                transition: 'background var(--dur-fast)',
              }}
            >
              {saved ? 'Saved' : 'Save Changes'}
            </button>
          </div>
        </div>
      </section>

      {/* ── Appearance ─────────────────────────────────────────── */}
      <section className="settings-section">
        <SectionHeader
          icon={<PaletteIcon />}
          title="Appearance"
          desc="Theme and display preferences."
        />

        <div className="settings-row">
          <div>
            <p className="settings-row__label">Color scheme</p>
            <p className="settings-row__hint">Follows your OS preference automatically. Dark mode is always active.</p>
          </div>
          <div className="settings-row__control" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--font-sm)', fontWeight: 600, color: 'var(--text-secondary)' }}>
            {isDark ? <><MoonIcon /> Dark</> : <><SunIcon /> Light</>}
          </div>
        </div>

        <div className="settings-row" style={{ borderBottom: 'none' }}>
          <div>
            <p className="settings-row__label">Compact tables</p>
            <p className="settings-row__hint">Reduce row padding in data tables.</p>
          </div>
          <div className="settings-row__control">
            <span style={{
              fontSize: '10px', fontWeight: 700, padding: '2px 8px',
              background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-full)', color: 'var(--text-disabled)',
            }}>
              Coming Soon
            </span>
          </div>
        </div>
      </section>

      {/* ── System Info ─────────────────────────────────────────── */}
      <section className="settings-section">
        <SectionHeader
          icon={<InfoIcon />}
          title="System"
          desc="Version and diagnostics."
        />
        <div className="info-grid">
          <div>
            <p className="info-item__label">Application</p>
            <p className="info-item__value">{import.meta.env.VITE_APP_NAME || 'AURA Analytics'} {import.meta.env.VITE_APP_VERSION || 'v1.0'}</p>
          </div>
          <div>
            <p className="info-item__label">Frontend</p>
            <p className="info-item__value">React 18 + Vite + TypeScript</p>
          </div>
          <div>
            <p className="info-item__label">Backend</p>
            <p className="info-item__value">FastAPI + DuckDB</p>
          </div>
          <div>
            <p className="info-item__label">AI Model</p>
            <p className="info-item__value">{import.meta.env.VITE_AI_MODEL_LABEL || 'Groq llama-3.3-70b (NL → SQL)'}</p>
          </div>
          <div>
            <p className="info-item__label">Charts</p>
            <p className="info-item__value">Recharts</p>
          </div>
          <div>
            <p className="info-item__label">Design</p>
            <p className="info-item__value">Dark-first enterprise · Inter + JetBrains Mono</p>
          </div>
        </div>
      </section>

      {/* ── Connector Catalog ───────────────────────────────────── */}
      <section className="settings-section">
        <SectionHeader
          icon={<LinkIcon />}
          title="Connector Catalog"
          desc="Data sources AURA can talk to. Greyed-out entries are missing their driver."
        />
        <div style={{ padding: 'var(--space-3) 0' }}>
          <ConnectorCatalog />
        </div>
      </section>

      {/* ── Danger Zone ─────────────────────────────────────────── */}
      <section className="settings-section danger-zone">
        <SectionHeader
          icon={<AlertTriangleIcon />}
          title="Danger Zone"
          desc="Irreversible actions — proceed with caution."
        />
        <div className="settings-row" style={{ borderBottom: 'none' }}>
          <div>
            <p className="settings-row__label">Clear all local data</p>
            <p className="settings-row__hint">Removes uploaded file references and query history from browser storage.</p>
          </div>
          <div className="settings-row__control">
            <button
              onClick={handleClearData}
              style={{
                padding: '6px 14px', background: 'var(--red-dim)',
                border: '1px solid var(--red-border)', borderRadius: 'var(--radius-md)',
                color: '#f87171', fontSize: 'var(--font-sm)', fontWeight: 600,
                cursor: 'pointer', fontFamily: 'var(--font-sans)',
              }}
            >
              Clear Data
            </button>
          </div>
        </div>
      </section>
    </div>
  );
};

export default Settings;
