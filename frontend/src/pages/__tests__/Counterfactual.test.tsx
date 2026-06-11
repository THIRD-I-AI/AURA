import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { sanitizeRecordHash } from '../../services/api';
import Counterfactual from '../Counterfactual';

describe('Counterfactual page', () => {
  it('renders the audience selector with all three tiers', () => {
    render(<Counterfactual />);
    expect(screen.getByTestId('audience-operator')).toBeInTheDocument();
    expect(screen.getByTestId('audience-auditor')).toBeInTheDocument();
    expect(screen.getByTestId('audience-analyst')).toBeInTheDocument();
  });

  it('updates the textarea when the user picks a different audience', async () => {
    const user = userEvent.setup();
    render(<Counterfactual />);
    const ta = screen.getByTestId('counterfactual-query-input') as HTMLTextAreaElement;
    expect(JSON.parse(ta.value).audience).toBe('operator');

    await user.click(screen.getByTestId('audience-auditor'));
    expect(JSON.parse(ta.value).audience).toBe('auditor');

    await user.click(screen.getByTestId('audience-analyst'));
    expect(JSON.parse(ta.value).audience).toBe('analyst');
  });

  it('does not show auditor-actions until a job has succeeded', () => {
    render(<Counterfactual />);
    // No record_hash yet → no PDF/replay/verify actions block
    expect(screen.queryByTestId('auditor-actions')).not.toBeInTheDocument();
    expect(screen.queryByTestId('download-pdf')).not.toBeInTheDocument();
  });

  // CodeQL #50-52 (js/xss-through-dom): audit_record_hash arrives from a
  // remote job-status response and feeds three <a href>s. Only a 64-char
  // lowercase sha256 hex may pass — same boundary rule the backend
  // enforces in exception_queue._index_path.
  it('sanitizeRecordHash accepts only 64-char lowercase sha256 hex', () => {
    const good = 'a'.repeat(64);
    expect(sanitizeRecordHash(good)).toBe(good);
    expect(sanitizeRecordHash(`javascript:alert(1)//${'a'.repeat(64)}`)).toBeNull();
    expect(sanitizeRecordHash('../../../etc/passwd')).toBeNull();
    expect(sanitizeRecordHash('A'.repeat(64))).toBeNull();   // uppercase
    expect(sanitizeRecordHash('a'.repeat(63))).toBeNull();   // wrong length
    expect(sanitizeRecordHash('')).toBeNull();
    expect(sanitizeRecordHash(null)).toBeNull();
    expect(sanitizeRecordHash(undefined)).toBeNull();
    expect(sanitizeRecordHash(42)).toBeNull();               // non-string
  });

  it('preserves user textarea edits when audience radio changes if JSON is malformed', async () => {
    const user = userEvent.setup();
    render(<Counterfactual />);
    const ta = screen.getByTestId('counterfactual-query-input') as HTMLTextAreaElement;
    // Type junk that isn't JSON
    await user.clear(ta);
    await user.type(ta, 'not valid json');
    await user.click(screen.getByTestId('audience-auditor'));
    // Junk text is preserved (radio still flips visually but the textarea
    // is left alone so the user's in-progress edit isn't clobbered).
    expect(ta.value).toBe('not valid json');
  });
});
