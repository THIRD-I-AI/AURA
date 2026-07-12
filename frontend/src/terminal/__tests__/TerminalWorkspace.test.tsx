import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

// Mock dockview-react: capture onReady, expose a fake api.
const added: string[] = [];
let layoutChangeCb: (() => void) | null = null;
vi.mock('dockview-react', () => ({
  DockviewReact: (props: { onReady: (e: unknown) => void }) => {
    const api = {
      addPanel: (o: { id: string }) => { added.push(o.id); return {}; },
      getPanel: (_id: string) => undefined,
      clear: () => {},
      fromJSON: () => {},
      toJSON: () => ({}),
      onDidLayoutChange: (cb: () => void) => { layoutChangeCb = cb; return { dispose() {} }; },
    };
    props.onReady({ api });
    return <div data-testid="dockview-mock" />;
  },
}));

import { TerminalWorkspace } from '../TerminalWorkspace';

describe('TerminalWorkspace', () => {
  it('mounts dockview and builds the default 4-panel layout', () => {
    added.length = 0;
    render(<MemoryRouter><TerminalWorkspace /></MemoryRouter>);
    expect(screen.getByTestId('dockview-mock')).toBeInTheDocument();
    expect(added).toEqual(expect.arrayContaining(['query', 'datasets', 'findings', 'livefeed']));
    expect(layoutChangeCb).toBeTypeOf('function'); // persistence wired
  });

  it('deep-links ?panel=pipeline to open the pipeline command deck', () => {
    added.length = 0;
    render(
      <MemoryRouter initialEntries={['/app/terminal?panel=pipeline']}>
        <TerminalWorkspace />
      </MemoryRouter>,
    );
    // getPanel returns undefined in the mock, so the requested panel is added.
    expect(added).toContain('pipeline');
  });

  it('ignores an unknown ?panel= value', () => {
    added.length = 0;
    render(
      <MemoryRouter initialEntries={['/app/terminal?panel=not-a-panel']}>
        <TerminalWorkspace />
      </MemoryRouter>,
    );
    expect(added).not.toContain('not-a-panel');
  });
});
