import type React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import type { PipelineTelemetry } from '../usePipelineTelemetry';
import { STATUS_GLYPH } from '../topology';

// Deterministic telemetry: api_gateway is monitored but has NO reading (the
// exact case the browser pass caught). No stream pipelines, empty rail.
const TELEMETRY: PipelineTelemetry = {
  serviceStatus: { uasr: 'healthy' }, // api_gateway intentionally absent
  overall: 'degraded',
  healthyServices: 1,
  totalServices: 8,
  huScore: 0.42,
  pipelines: [],
  events: [],
  connected: false,
  lastUpdate: new Date(0).toISOString(),
};

vi.mock('../usePipelineTelemetry', async (importActual) => {
  const actual = await importActual<typeof import('../usePipelineTelemetry')>();
  return { ...actual, usePipelineTelemetry: () => TELEMETRY };
});

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    streamingService: { start: vi.fn(), stop: vi.fn(), pause: vi.fn(), resume: vi.fn() },
    healingService: { pending: vi.fn().mockResolvedValue([]), approve: vi.fn(), reject: vi.fn() },
  };
});

import PipelinePanel from '../../panels/PipelinePanel';

// PipelinePanel ignores its dockview props (_props); an empty object cast to
// the panel's prop type is enough to mount it. Cast through `object` (not
// `never`, which is not a valid spread source under tsc).
const props = { api: {}, params: {}, containerApi: {} } as unknown as React.ComponentProps<
  typeof PipelinePanel
>;

describe('PipelinePanel render contract', () => {
  it('mounts without throwing and shows the deck', () => {
    render(<PipelinePanel {...props} />);
    expect(screen.getByTestId('pipeline-panel')).toBeInTheDocument();
  });

  it('DAG scales to its panel: SVG has a viewBox (not a fixed px canvas only)', () => {
    const { container } = render(<PipelinePanel {...props} />);
    const svg = container.querySelector('svg.pl-graph');
    expect(svg, 'pl-graph svg present').not.toBeNull();
    // A viewBox is what lets the measured pixel size scale the DAG. The y
    // origin may be negative: the stage-label band sits above the graph.
    expect(svg!.getAttribute('viewBox')).toMatch(/^0 -?\d+ \d+ \d+$/);
  });

  it('renders the honest "unknown" glyph for a monitored service with no reading', () => {
    const { container } = render(<PipelinePanel {...props} />);
    const glyphs = Array.from(container.querySelectorAll('text.pl-node-glyph'))
      .map((t) => t.textContent);
    // api_gateway is monitored but absent from serviceStatus -> must be the
    // 'unknown' glyph, never the 'unmonitored' one.
    expect(glyphs).toContain(STATUS_GLYPH.unknown);
  });
});
