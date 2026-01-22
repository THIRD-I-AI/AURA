/**
 * InsightsViewer Component
 * Display auto-generated insights, charts, and narratives
 */

import React, { useState } from 'react';
import './InsightsViewer.css';

interface Insight {
  type: string;
  title: string;
  description: string;
  metric_name?: string;
  metric_value?: number;
  confidence: number;
}

interface Chart {
  type: string;
  title: string;
  data: any[];
  config: Record<string, any>;
}

interface InsightsData {
  insights: Insight[];
  charts: Chart[];
  narrative: string;
  row_count: number;
}

interface Props {
  data?: InsightsData;
  loading?: boolean;
}

const InsightsViewer: React.FC<Props> = ({ data, loading = false }) => {
  const [selectedInsight, setSelectedInsight] = useState<number>(0);
  const [chartType, setChartType] = useState<'chart' | 'narrative'>('chart');

  const displayData = data || {
    insights: [],
    charts: [],
    narrative: 'No insights available yet. Run a query to generate insights.',
    row_count: 0,
  };

  return (
    <div className="insights-viewer">
      <div className="insights-header">
        <h2>💡 Insights & Analysis</h2>
        <div className="view-toggle">
          <button
            className={`toggle-btn ${chartType === 'chart' ? 'active' : ''}`}
            onClick={() => setChartType('chart')}
          >
            📊 Charts
          </button>
          <button
            className={`toggle-btn ${chartType === 'narrative' ? 'active' : ''}`}
            onClick={() => setChartType('narrative')}
          >
            📝 Narrative
          </button>
        </div>
      </div>

      <div className="insights-container">
        {/* Insights Sidebar */}
        <div className="insights-sidebar">
          <h3>Discovered Insights</h3>
          <div className="insights-list">
            {displayData.insights.map((insight, idx) => (
              <div
                key={idx}
                className={`insight-card ${selectedInsight === idx ? 'active' : ''}`}
                onClick={() => setSelectedInsight(idx)}
              >
                <div className="insight-badge">{insight.type[0].toUpperCase()}</div>
                <div className="insight-content">
                  <div className="insight-title">{insight.title}</div>
                  <div className="insight-confidence">
                    Confidence: {(insight.confidence * 100).toFixed(0)}%
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Main Content */}
        <div className="insights-main">
          {loading ? (
            <div className="loading">Analyzing data...</div>
          ) : chartType === 'chart' ? (
            <div className="chart-view">
              {displayData.charts.length > 0 ? (
                <>
                  <div className="chart-container">
                    <div className="chart-placeholder">
                      <div className="chart-title">{displayData.charts[0].title}</div>
                      <svg viewBox="0 0 400 250" className="sparkline">
                        <polyline
                          points="0,200 50,150 100,180 150,100 200,120 250,80 300,90 350,40 400,60"
                          fill="none"
                          stroke="url(#gradient)"
                          strokeWidth="2"
                        />
                        <defs>
                          <linearGradient id="gradient" x1="0%" y1="0%" x2="100%" y2="0%">
                            <stop offset="0%" stopColor="#64c8ff" />
                            <stop offset="100%" stopColor="#ff64b4" />
                          </linearGradient>
                        </defs>
                      </svg>
                    </div>
                  </div>

                  <div className="selected-insight">
                    <h3>{displayData.insights[selectedInsight].title}</h3>
                    <p>{displayData.insights[selectedInsight].description}</p>
                    {displayData.insights[selectedInsight].metric_value && (
                      <div className="metric-display">
                        <span className="metric-label">
                          {displayData.insights[selectedInsight].metric_name}
                        </span>
                        <span className="metric-value">
                          {displayData.insights[selectedInsight].metric_value?.toLocaleString()}
                        </span>
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <div className="empty-state">No charts to display</div>
              )}
            </div>
          ) : (
            <div className="narrative-view">
              <div className="narrative-card">
                <h3>Analysis Narrative</h3>
                <p>{displayData.narrative}</p>

                <div className="summary-stats">
                  <div className="stat-box">
                    <div className="stat-number">{displayData.row_count.toLocaleString()}</div>
                    <div className="stat-label">Records Analyzed</div>
                  </div>
                  <div className="stat-box">
                    <div className="stat-number">{displayData.insights.length}</div>
                    <div className="stat-label">Insights Generated</div>
                  </div>
                  <div className="stat-box">
                    <div className="stat-number">{displayData.charts.length}</div>
                    <div className="stat-label">Charts Created</div>
                  </div>
                </div>

                <div className="insights-summary">
                  <h4>Key Findings</h4>
                  <ul>
                    {displayData.insights.map((insight, idx) => (
                      <li key={idx}>
                        <span className="bullet">▸</span>
                        <strong>{insight.title}:</strong> {insight.description}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default InsightsViewer;
