import React from 'react';
import './NavigationBar.css';
import ThemeToggle from './ThemeToggle';

interface NavigationBarProps {
  currentMode: 'chat' | 'database' | 'visualization' | 'strategic' | 'pipelines';
  onModeChange: (mode: 'chat' | 'database' | 'visualization' | 'strategic' | 'pipelines') => void;
}

const NavigationBar: React.FC<NavigationBarProps> = ({ currentMode, onModeChange }) => {

  return (
    <nav className="navigation-bar">
      <div className="nav-left">
        <div className="nav-brand">
          <div className="brand-logo">
            <div className="logo-icon">🚀</div>
            <div className="logo-glow"></div>
          </div>
          <div className="brand-text">
            <h1>AURA</h1>
            <p>Advanced Unified Research Analytics</p>
            <div className="brand-tagline">Powered by AI Intelligence</div>
          </div>
        </div>
        <div className="connection-status">
          <div className="status-dot online"></div>
          <span className="status-text">All Systems Online</span>
        </div>
      </div>

      <div className="nav-center">
        <div className="mode-selector">
          <button 
            className={`mode-btn ${currentMode === 'chat' ? 'active' : ''}`}
            onClick={() => onModeChange('chat')}
          >
            <span className="icon">💬</span>
            <span className="label">Chat</span>
          </button>
          <button 
            className={`mode-btn ${currentMode === 'database' ? 'active' : ''}`}
            onClick={() => onModeChange('database')}
          >
            <span className="icon">🗄️</span>
            <span className="label">Database</span>
          </button>
          <button 
            className={`mode-btn ${currentMode === 'pipelines' ? 'active' : ''}`}
            onClick={() => onModeChange('pipelines')}
          >
            <span className="icon">🚀</span>
            <span className="label">Pipelines</span>
          </button>
          <button 
            className={`mode-btn ${currentMode === 'visualization' ? 'active' : ''}`}
            onClick={() => onModeChange('visualization')}
          >
            <span className="icon">📊</span>
            <span className="label">Visualize</span>
          </button>
        </div>
      </div>

      <div className="nav-controls">
        <ThemeToggle />
      </div>
    </nav>
  );
};

export default NavigationBar;