import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HashChip } from '../HashChip';

const HASH = 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90';

describe('ui/HashChip', () => {
  it('middle-truncates and copies the FULL hash', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<HashChip hash={HASH} />);
    expect(screen.getByText('a1b2c3d4…7e8f90')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /copy/i }));
    expect(writeText).toHaveBeenCalledWith(HASH);
  });

  it('renders a verify link only for a sanitized hash', () => {
    render(<HashChip hash={HASH} verifyHref={`/verify/${HASH}`} />);
    expect(screen.getByRole('link', { name: /verify/i })).toHaveAttribute('href', `/verify/${HASH}`);
  });

  it('refuses to render a link for a non-hex hash (Sec-6 boundary)', () => {
    render(<HashChip hash={'javascript:alert(1)'} verifyHref="/verify/javascript:alert(1)" />);
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getByText('invalid hash')).toBeInTheDocument();
  });
});
