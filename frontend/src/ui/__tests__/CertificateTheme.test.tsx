import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CertificateTheme } from '../CertificateTheme';

describe('CertificateTheme', () => {
  it('scopes data-theme="certificate" to a wrapper, never documentElement', () => {
    render(<CertificateTheme><p>doc body</p></CertificateTheme>);
    const wrapper = screen.getByText('doc body').closest('[data-theme="certificate"]');
    expect(wrapper).not.toBeNull();
    // The product theme on <html> must be untouched (certificates are
    // a local island — the app around them stays dark).
    expect(document.documentElement.getAttribute('data-theme')).not.toBe('certificate');
  });
});
