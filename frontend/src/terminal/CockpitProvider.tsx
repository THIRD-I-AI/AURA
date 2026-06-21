import { createContext, useContext, useReducer, useCallback, useMemo, type ReactNode } from 'react';

export interface CockpitState { activeDataset: string | null }
type CockpitAction = { type: 'SET_ACTIVE_DATASET'; name: string | null };

function reducer(state: CockpitState, action: CockpitAction): CockpitState {
  switch (action.type) {
    case 'SET_ACTIVE_DATASET':
      return { ...state, activeDataset: action.name };
    default:
      return state;
  }
}

interface CockpitContextValue {
  activeDataset: string | null;
  setActiveDataset: (name: string | null) => void;
}

const CockpitContext = createContext<CockpitContextValue | null>(null);

export function CockpitProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, { activeDataset: null });
  const setActiveDataset = useCallback((name: string | null) => {
    dispatch({ type: 'SET_ACTIVE_DATASET', name });
  }, []);
  const value = useMemo(
    () => ({ activeDataset: state.activeDataset, setActiveDataset }),
    [state.activeDataset, setActiveDataset],
  );
  return <CockpitContext.Provider value={value}>{children}</CockpitContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useCockpit(): CockpitContextValue {
  const ctx = useContext(CockpitContext);
  if (!ctx) throw new Error('useCockpit must be used within a CockpitProvider');
  return ctx;
}
