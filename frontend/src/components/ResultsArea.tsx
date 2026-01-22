import React from 'react';
import { useTheme } from '../contexts/ThemeContext';
import SqlDisplay from './SqlDisplay';
import DataTable from './DataTable';
import type { DataResult } from '../types';

interface ResultsAreaProps {
  sqlQuery: string;
  dataResult: DataResult | null;
  pendingApproval?: boolean;
  onApproval?: (approved: boolean, editedQuery?: string) => void;
  isLoading?: boolean;
}

const ResultsArea: React.FC<ResultsAreaProps> = ({ 
  sqlQuery, 
  dataResult, 
  pendingApproval = false, 
  onApproval,
  isLoading = false
}) => {
  const { theme } = useTheme();
  
  return (
    <div className="results-area" data-theme={theme}>
      <SqlDisplay 
        sqlQuery={sqlQuery} 
        pendingApproval={pendingApproval}
        onApproval={onApproval}
        isEditable={true}
        showVersionHistory={true}
      />
      {isLoading && (
        <div className="loading-indicator">
          <div className="spinner"></div>
          <p>Executing query...</p>
        </div>
      )}
      {dataResult && !pendingApproval && (
        <DataTable data={dataResult} />
      )}
    </div>
  );
};

export default ResultsArea;