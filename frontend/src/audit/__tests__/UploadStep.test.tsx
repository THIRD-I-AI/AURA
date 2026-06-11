import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { UploadStep } from '../wizard/UploadStep';

const base = {
  file: null,
  columns: [],
  previewRows: [],
  types: {},
  uploading: false,
  error: null,
  onPick: () => {},
};

// S35c: the customer-facing wizard rendered a native unstyled file
// input. It must be a themed drop-zone, while keeping the hidden
// input (testid wizard-file-input) for programmatic uploads.
describe('UploadStep drop-zone', () => {
  it('renders the drop-zone with the file input still wired', () => {
    render(<UploadStep {...base} />);
    expect(screen.getByTestId('wizard-dropzone')).toBeInTheDocument();
    expect(screen.getByTestId('wizard-file-input')).toBeInTheDocument();
  });

  it('accepts a dropped CSV', () => {
    const onPick = vi.fn();
    render(<UploadStep {...base} onPick={onPick} />);
    const f = new File(['a,b\n1,2'], 'decisions.csv', { type: 'text/csv' });
    fireEvent.drop(screen.getByTestId('wizard-dropzone'), {
      dataTransfer: { files: [f] },
    });
    expect(onPick).toHaveBeenCalledWith(f);
  });

  it('shows the chosen filename inside the zone', () => {
    const f = new File(['a'], 'decisions.csv', { type: 'text/csv' });
    render(<UploadStep {...base} file={f} />);
    expect(screen.getByTestId('wizard-dropzone')).toHaveTextContent('decisions.csv');
  });
});
