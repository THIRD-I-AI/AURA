import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { LineageSummaryCard } from '../cockpit/LineageSummaryCard';
import { PipelinesStreamingPanel } from '../cockpit/PipelinesStreamingPanel';
import { WorkbenchNav } from '../WorkbenchNav';

// BUG-257: status text with no data behind it, a count shown against the wrong
// queue, and a link that redirected straight back to the page it was on.
describe('BUG-257: Workbench chrome shows only what is real', () => {
  it('the pipelines card makes no PII-masking claim it has no data for', () => {
    render(<PipelinesStreamingPanel pipelines={[]} onDefinePipeline={() => undefined} />);
    expect(screen.queryByText(/pii masking/i)).not.toBeInTheDocument();
  });

  it('the Constellation link goes to the terminal, where the graph lives', () => {
    render(<LineageSummaryCard ledger={null} />);
    // /app redirects to /workbench -- the page this card is already on.
    expect(screen.getByRole('link', { name: /open constellation/i })).toHaveAttribute('href', '/app/terminal');
  });

  it('the healing queue count badges Healing Queue only', () => {
    render(
      <WorkbenchNav navOpen={false} onCloseNav={() => undefined} navCollapsed={false} onToggleCollapsed={() => undefined}
        nav="Cockpit" selectNav={() => undefined} pendingCount={3} ledger={null} ledgerDown={false} />,
    );
    expect(within(screen.getByRole('button', { name: /healing queue/i })).getByText('3')).toBeInTheDocument();
    expect(within(screen.getByRole('button', { name: /exception queue/i })).queryByText('3')).not.toBeInTheDocument();
  });
});
