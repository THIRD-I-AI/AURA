import React from 'react';

/**
 * Scopes the light, print-safe certificate theme to its children.
 * Certificates are documents — they render light even while the product
 * around them is dark. Never touches documentElement (that belongs to
 * ThemeContext).
 */
export const CertificateTheme: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div data-theme="certificate" className="certificate-root">
    {children}
  </div>
);
