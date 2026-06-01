import { useEffect, useState } from 'react';
import { parseCsvHeadAndRows, type ColumnType } from './csv';

export interface CsvPreview {
  columns: string[];
  previewRows: string[][];
  types: Record<string, ColumnType>;
  error: string | null;
  loading: boolean;
}

const EMPTY: CsvPreview = { columns: [], previewRows: [], types: {}, error: null, loading: false };

export function useCsvPreview(file: File | null): CsvPreview {
  const [state, setState] = useState<CsvPreview>(EMPTY);

  useEffect(() => {
    if (!file) { setState(EMPTY); return; }
    let cancelled = false;
    setState({ ...EMPTY, loading: true });
    const reader = new FileReader();
    reader.onload = () => {
      if (cancelled) return;
      try {
        const head = parseCsvHeadAndRows(String(reader.result ?? ''));
        setState({ columns: head.columns, previewRows: head.rows, types: head.types, error: null, loading: false });
      } catch (e) {
        setState({ ...EMPTY, error: e instanceof Error ? e.message : String(e) });
      }
    };
    reader.onerror = () => { if (!cancelled) setState({ ...EMPTY, error: 'Could not read file.' }); };
    reader.readAsText(file);
    return () => { cancelled = true; };
  }, [file]);

  return state;
}
