import { describe, expect, it } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useCsvPreview } from '../useCsvPreview';

describe('useCsvPreview', () => {
  it('parses columns/rows/types from a selected File', async () => {
    const file = new File(['age,name\n30,alice\n40,bob\n'], 'd.csv', { type: 'text/csv' });
    const { result } = renderHook(() => useCsvPreview(file));
    await waitFor(() => expect(result.current.columns).toEqual(['age', 'name']));
    expect(result.current.types).toEqual({ age: 'number', name: 'string' });
    expect(result.current.previewRows[0]).toEqual(['30', 'alice']);
  });

  it('resets to empty when file is null', () => {
    const { result } = renderHook(() => useCsvPreview(null));
    expect(result.current.columns).toEqual([]);
    expect(result.current.error).toBeNull();
  });
});
