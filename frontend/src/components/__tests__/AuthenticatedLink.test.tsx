import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchProtectedFile, setAuthToken } from '../../services/api';
import { AuthenticatedLink } from '../AuthenticatedLink';

/** Download and report links used to be plain navigations, which cannot carry the
 *  bearer token, so with auth on they opened a 401 instead of the file. */
describe('files behind the JWT gate', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let openMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setAuthToken('tok-abc');
    fetchMock = vi.fn(() => Promise.resolve(new Response(new Blob(['%PDF-1.4']), { status: 200 })));
    openMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('open', openMock);
    URL.createObjectURL = vi.fn(() => 'blob:mock-object-url');
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it('fetches the file with the bearer token instead of navigating to it', async () => {
    const user = userEvent.setup();
    render(<AuthenticatedLink href="/api/v1/counterfactual/artifacts/abc/report.pdf" data-testid="pdf">PDF</AuthenticatedLink>);

    await user.click(screen.getByTestId('pdf'));

    await waitFor(() => expect(openMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/counterfactual/artifacts/abc/report.pdf');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-abc');
    // The tab is opened on the fetched blob, never on the protected URL itself.
    expect(openMock).toHaveBeenCalledWith('blob:mock-object-url', '_blank', 'noopener');
  });

  it('saves under the given name when downloadAs is set', async () => {
    const clicked: string[] = [];
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = realCreate(tag);
      if (tag === 'a') (el as HTMLAnchorElement).click = () => { clicked.push((el as HTMLAnchorElement).download); };
      return el;
    });

    await fetchProtectedFile('/api/v1/pipeline/download/out.csv', { downloadAs: 'out.csv' });

    expect(clicked).toEqual(['out.csv']);
    expect(openMock).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  it('shows why it failed instead of opening an error page', async () => {
    fetchMock.mockResolvedValueOnce(new Response('{"error":"NOT_FOUND"}', { status: 404 }));
    const user = userEvent.setup();
    render(<AuthenticatedLink href="/api/v1/x/report.pdf" data-testid="pdf">PDF</AuthenticatedLink>);

    await user.click(screen.getByTestId('pdf'));

    expect(await screen.findByTestId('pdf-error')).toHaveTextContent(/not found/i);
    expect(openMock).not.toHaveBeenCalled();
  });

  it('reports an expired session on a 401', async () => {
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 }));

    await expect(fetchProtectedFile('/api/v1/etl/download/out.csv')).rejects.toThrow(/session has expired/i);
  });
});
