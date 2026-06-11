import React, { useState } from 'react';

// Mock types
interface AuditException {
  id: string;
  pcaobStandard: string;
  description: string;
  riskLevel: 'Low' | 'Medium' | 'High' | 'Critical';
  aiRecordHash: string;
}

const mockExceptions: AuditException[] = [
  {
    id: 'exc-001',
    pcaobStandard: 'AS 2305',
    description: 'Significant statistical variance detected for account 6001 (Marketing Expense). Amount: $150,000.',
    riskLevel: 'High',
    aiRecordHash: 'abc123hashvalue...',
  },
  {
    id: 'exc-002',
    pcaobStandard: 'AS 2401',
    description: 'Suspicious round-dollar journal entry detected. Amount: $50,000.',
    riskLevel: 'Critical',
    aiRecordHash: 'def456hashvalue...',
  }
];

export const ExceptionQueue: React.FC<{ isShadowMode?: boolean }> = ({ isShadowMode = false }) => {
  const [exceptions, setExceptions] = useState<AuditException[]>(mockExceptions);
  const [rationale, setRationale] = useState<string>('');
  const [selectedException, setSelectedException] = useState<AuditException | null>(null);

  const handleOverride = async (approved: boolean) => {
    if (!selectedException || isShadowMode) return;
    
    // In production, this hits the backend which calls `audit_human_override`
    // securing the PCAOB AS 1215 contradiction documentation. The auditor's
    // identity is NOT sent in the body — the backend binds it to the
    // verified JWT's `sub` claim (anti-impersonation, fail-closed).
    console.log(`Submitting Override:`, {
      aiRecordHash: selectedException.aiRecordHash,
      rationale,
      approved
    });

    // Remove from queue
    setExceptions(prev => prev.filter(e => e.id !== selectedException.id));
    setSelectedException(null);
    setRationale('');
  };

  return (
    <div className="p-6 bg-gray-900 text-white min-h-screen">
      <div className="flex justify-between items-center mb-4">
        <h1 className="text-3xl font-bold">Internal HITL Workbench</h1>
        {isShadowMode && (
          <div className="bg-yellow-600 text-white px-4 py-2 rounded font-bold shadow animate-pulse">
            SHADOW MODE ACTIVE: READ-ONLY EMPIRICAL RECORD
          </div>
        )}
      </div>
      <p className="text-gray-400 mb-8">PCAOB AS 1215 Exception Review Queue</p>

      <div className="grid grid-cols-2 gap-8">
        <div>
          <h2 className="text-xl font-semibold mb-4">Pending Exceptions</h2>
          {exceptions.map(exc => (
            <div 
              key={exc.id} 
              className={`p-4 mb-4 border rounded cursor-pointer ${selectedException?.id === exc.id ? 'border-blue-500 bg-gray-800' : 'border-gray-700 bg-gray-800'}`}
              onClick={() => setSelectedException(exc)}
            >
              <div className="flex justify-between">
                <span className="font-bold text-blue-400">{exc.pcaobStandard}</span>
                <span className={`px-2 py-1 text-xs rounded ${exc.riskLevel === 'Critical' ? 'bg-red-900 text-red-200' : 'bg-yellow-900 text-yellow-200'}`}>
                  {exc.riskLevel} Risk
                </span>
              </div>
              <p className="mt-2 text-sm">{exc.description}</p>
            </div>
          ))}
          {exceptions.length === 0 && <p className="text-green-400">All exceptions cleared. Audit ready for ED25519 signature.</p>}
        </div>

        {selectedException && (
          <div className="p-6 bg-gray-800 border border-gray-700 rounded h-fit">
            <h2 className="text-xl font-semibold mb-4">Review Finding</h2>
            <p className="mb-4 text-gray-300">{selectedException.description}</p>
            
            <div className="mb-4">
              <label className="block text-sm font-medium mb-2 text-gray-400">Auditor Rationale (AS 1215 Contradiction Record)</label>
              <textarea 
                className="w-full p-2 bg-gray-900 border border-gray-700 rounded text-white"
                rows={4}
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                placeholder="Explain why you are overriding or approving this AI finding..."
              />
            </div>

            <div className="flex gap-4">
              {isShadowMode ? (
                <div className="p-4 bg-gray-700 rounded text-sm text-yellow-300 w-full">
                  ⚠️ Action disabled in Shadow Mode. This anomaly is logged for Model Risk Management evaluation. 
                </div>
              ) : (
                <>
                  <button 
                    onClick={() => handleOverride(true)}
                    className="px-4 py-2 bg-green-700 hover:bg-green-600 rounded font-semibold transition"
                  >
                    Approve AI Finding
                  </button>
                  <button 
                    onClick={() => handleOverride(false)}
                    className="px-4 py-2 bg-red-700 hover:bg-red-600 rounded font-semibold transition"
                  >
                    Override AI Finding
                  </button>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
