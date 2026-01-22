import React, { useState, useEffect } from 'react';
import { Bar, Line, Pie } from 'react-chartjs-2';
import './TrendAnalysis.css';

interface TrendAnalysisProps {
  data: any[];
  fileName: string;
}

interface TrendInsight {
  type: 'growth' | 'decline' | 'pattern' | 'anomaly' | 'correlation';
  title: string;
  description: string;
  confidence: number;
  value?: string;
  icon: string;
}

interface ChartData {
  type: 'bar' | 'line' | 'pie';
  title: string;
  data: any;
  insight: string;
}

const TrendAnalysis: React.FC<TrendAnalysisProps> = ({ data, fileName }) => {
  const [insights, setInsights] = useState<TrendInsight[]>([]);
  const [charts, setCharts] = useState<ChartData[]>([]);
  const [isAnalyzing, setIsAnalyzing] = useState(false);

  useEffect(() => {
    if (data && data.length > 0) {
      analyzeTrends();
    }
  }, [data]);

  const analyzeTrends = async () => {
    setIsAnalyzing(true);
    
    // Simulate analysis delay
    await new Promise(resolve => setTimeout(resolve, 2000));
    
    try {
      const generatedInsights = generateInsights(data);
      const generatedCharts = generateCharts(data);
      
      setInsights(generatedInsights);
      setCharts(generatedCharts);
    } catch (error) {
      console.error('Error analyzing trends:', error);
    } finally {
      setIsAnalyzing(false);
    }
  };

  const generateInsights = (dataset: any[]): TrendInsight[] => {
    const insights: TrendInsight[] = [];
    const columns = Object.keys(dataset[0]);
    const numericColumns = columns.filter(col => 
      dataset.some(row => typeof row[col] === 'number')
    );

    // Data size insight
    insights.push({
      type: 'pattern',
      title: 'Dataset Overview',
      description: `Analyzed ${dataset.length} records with ${columns.length} attributes`,
      confidence: 100,
      value: `${dataset.length} rows × ${columns.length} columns`,
      icon: '📊'
    });

    // Numeric column analysis
    if (numericColumns.length > 0) {
      numericColumns.forEach(col => {
        const values = dataset.map(row => row[col]).filter(val => typeof val === 'number');
        if (values.length > 0) {
          const sum = values.reduce((a, b) => a + b, 0);
          const avg = sum / values.length;
          
          // Growth trend detection
          if (values.length > 1) {
            const firstHalf = values.slice(0, Math.floor(values.length / 2));
            const secondHalf = values.slice(Math.floor(values.length / 2));
            const firstAvg = firstHalf.reduce((a, b) => a + b, 0) / firstHalf.length;
            const secondAvg = secondHalf.reduce((a, b) => a + b, 0) / secondHalf.length;
            
            if (secondAvg > firstAvg * 1.1) {
              insights.push({
                type: 'growth',
                title: `${col} Shows Growth Trend`,
                description: `${col} increased by ${((secondAvg / firstAvg - 1) * 100).toFixed(1)}% across the dataset`,
                confidence: 85,
                value: `+${((secondAvg / firstAvg - 1) * 100).toFixed(1)}%`,
                icon: '📈'
              });
            } else if (secondAvg < firstAvg * 0.9) {
              insights.push({
                type: 'decline',
                title: `${col} Shows Declining Trend`,
                description: `${col} decreased by ${((1 - secondAvg / firstAvg) * 100).toFixed(1)}% across the dataset`,
                confidence: 85,
                value: `-${((1 - secondAvg / firstAvg) * 100).toFixed(1)}%`,
                icon: '📉'
              });
            }
          }

          // Outlier detection
          const variance = values.reduce((acc, val) => acc + Math.pow(val - avg, 2), 0) / values.length;
          const stdDev = Math.sqrt(variance);
          const outliers = values.filter(val => Math.abs(val - avg) > 2 * stdDev);
          
          if (outliers.length > 0) {
            insights.push({
              type: 'anomaly',
              title: `Outliers Detected in ${col}`,
              description: `Found ${outliers.length} potential outliers that deviate significantly from the average`,
              confidence: 75,
              value: `${outliers.length} outliers`,
              icon: '⚠️'
            });
          }
        }
      });
    }

    // Category analysis
    const categoryColumns = columns.filter(col => 
      dataset.every(row => typeof row[col] === 'string')
    );

    categoryColumns.forEach(col => {
      const uniqueValues = [...new Set(dataset.map(row => row[col]))];
      if (uniqueValues.length < dataset.length * 0.5) {
        const valueCounts = uniqueValues.map(val => ({
          value: val,
          count: dataset.filter(row => row[col] === val).length
        })).sort((a, b) => b.count - a.count);

        insights.push({
          type: 'pattern',
          title: `Top Category in ${col}`,
          description: `"${valueCounts[0].value}" is the most frequent value, appearing ${valueCounts[0].count} times`,
          confidence: 90,
          value: `${valueCounts[0].value} (${((valueCounts[0].count / dataset.length) * 100).toFixed(1)}%)`,
          icon: '🏆'
        });
      }
    });

    return insights.slice(0, 6); // Limit to 6 insights
  };

  const generateCharts = (dataset: any[]): ChartData[] => {
    const charts: ChartData[] = [];
    const columns = Object.keys(dataset[0]);
    const numericColumns = columns.filter(col => 
      dataset.some(row => typeof row[col] === 'number')
    );

    // Generate bar chart for first numeric column
    if (numericColumns.length > 0) {
      const col = numericColumns[0];
      const values = dataset.map((row, index) => ({ x: `Item ${index + 1}`, y: row[col] }));
      
      charts.push({
        type: 'bar',
        title: `${col} Distribution`,
        data: {
          labels: values.map(v => v.x),
          datasets: [{
            label: col,
            data: values.map(v => v.y),
            backgroundColor: 'rgba(59, 130, 246, 0.6)',
            borderColor: 'rgba(59, 130, 246, 1)',
            borderWidth: 1
          }]
        },
        insight: `Bar chart showing the distribution of ${col} across all records`
      });

      // Generate line chart if we have time-series like data
      if (dataset.length > 5) {
        charts.push({
          type: 'line',
          title: `${col} Trend Line`,
          data: {
            labels: values.map(v => v.x),
            datasets: [{
              label: col,
              data: values.map(v => v.y),
              borderColor: 'rgba(139, 92, 246, 1)',
              backgroundColor: 'rgba(139, 92, 246, 0.1)',
              tension: 0.4,
              fill: true
            }]
          },
          insight: `Trend line visualization showing patterns in ${col} over the dataset sequence`
        });
      }
    }

    // Generate pie chart for categorical data
    const categoryColumns = columns.filter(col => 
      dataset.every(row => typeof row[col] === 'string')
    );

    if (categoryColumns.length > 0) {
      const col = categoryColumns[0];
      const uniqueValues = [...new Set(dataset.map(row => row[col]))];
      
      if (uniqueValues.length <= 10 && uniqueValues.length > 1) {
        const valueCounts = uniqueValues.map(val => ({
          value: val,
          count: dataset.filter(row => row[col] === val).length
        }));

        const colors = [
          '#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444',
          '#06b6d4', '#84cc16', '#f97316', '#ec4899', '#6366f1'
        ];

        charts.push({
          type: 'pie',
          title: `${col} Distribution`,
          data: {
            labels: valueCounts.map(vc => vc.value),
            datasets: [{
              data: valueCounts.map(vc => vc.count),
              backgroundColor: colors.slice(0, valueCounts.length),
              borderWidth: 2,
              borderColor: '#ffffff'
            }]
          },
          insight: `Pie chart showing the proportion of different values in ${col}`
        });
      }
    }

    return charts.slice(0, 3); // Limit to 3 charts
  };

  const renderChart = (chartData: ChartData) => {
    const options = {
      responsive: true,
      plugins: {
        legend: {
          position: 'top' as const,
        },
        title: {
          display: true,
          text: chartData.title
        }
      }
    };

    switch (chartData.type) {
      case 'bar':
        return <Bar data={chartData.data} options={options} />;
      case 'line':
        return <Line data={chartData.data} options={options} />;
      case 'pie':
        return <Pie data={chartData.data} options={options} />;
      default:
        return null;
    }
  };

  return (
    <div className="trend-analysis">
      <div className="analysis-header">
        <h3>🔍 AI Trend Analysis</h3>
        <p>Automated insights for <strong>{fileName}</strong></p>
      </div>

      {isAnalyzing ? (
        <div className="analysis-loading">
          <div className="analysis-spinner"></div>
          <h4>Analyzing Data Patterns...</h4>
          <p>Generating insights and visualizations</p>
        </div>
      ) : (
        <>
          {insights.length > 0 && (
            <div className="insights-section">
              <h4>📊 Key Insights</h4>
              <div className="insights-grid">
                {insights.map((insight, index) => (
                  <div key={index} className={`insight-card ${insight.type}`}>
                    <div className="insight-header">
                      <span className="insight-icon">{insight.icon}</span>
                      <div className="insight-confidence">
                        {insight.confidence}% confidence
                      </div>
                    </div>
                    <h5>{insight.title}</h5>
                    <p>{insight.description}</p>
                    {insight.value && (
                      <div className="insight-value">{insight.value}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {charts.length > 0 && (
            <div className="charts-section">
              <h4>📈 Generated Visualizations</h4>
              <div className="charts-grid">
                {charts.map((chart, index) => (
                  <div key={index} className="chart-container">
                    <div className="chart-wrapper">
                      {renderChart(chart)}
                    </div>
                    <div className="chart-insight">
                      <p>{chart.insight}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default TrendAnalysis;