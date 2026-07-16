import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Panel, PanelHeader, PanelBody } from '../panel'
import { StatusGlyph } from '../status-glyph'
import { EmptyState } from '../empty-state'

describe('StatusGlyph — honest state semantics', () => {
  it('awaiting is a distinct monitored-no-data state (◌), never ok/green', () => {
    render(<StatusGlyph status="awaiting" />)
    const el = screen.getByRole('img')
    expect(el.textContent).toBe('◌')
    // honest label, and NOT the healthy label
    expect(el.getAttribute('aria-label')).toMatch(/awaiting/i)
    expect(el.getAttribute('aria-label')).not.toMatch(/healthy/i)
  })

  it('unmonitored (·) is distinct from awaiting (◌)', () => {
    const { rerender } = render(<StatusGlyph status="unmonitored" />)
    expect(screen.getByRole('img').textContent).toBe('·')
    rerender(<StatusGlyph status="awaiting" />)
    expect(screen.getByRole('img').textContent).toBe('◌')
  })

  it('maps health states to filled dot with correct labels', () => {
    const cases: Array<['ok' | 'warn' | 'error', RegExp]> = [
      ['ok', /healthy/i],
      ['warn', /degraded/i],
      ['error', /failed/i],
    ]
    for (const [status, label] of cases) {
      const { unmount } = render(<StatusGlyph status={status} />)
      const el = screen.getByRole('img')
      expect(el.textContent).toBe('●')
      expect(el.getAttribute('aria-label')).toMatch(label)
      unmount()
    }
  })

  it('honors a custom accessible label', () => {
    render(<StatusGlyph status="ok" label="gateway online" />)
    expect(screen.getByRole('img').getAttribute('aria-label')).toBe('gateway online')
  })
})

describe('Panel', () => {
  it('composes header (title/hint/actions) and body', () => {
    render(
      <Panel>
        <PanelHeader
          title="pipeline"
          hint="live"
          glyph={<StatusGlyph status="ok" />}
          actions={<button type="button">refresh</button>}
        />
        <PanelBody>content</PanelBody>
      </Panel>,
    )
    expect(screen.getByText('pipeline')).toBeTruthy()
    expect(screen.getByText('live')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'refresh' })).toBeTruthy()
    expect(screen.getByText('content')).toBeTruthy()
  })

  it('PanelBody drops padding when flush', () => {
    const { container } = render(<PanelBody flush>x</PanelBody>)
    const el = container.querySelector('[data-slot="panel-body"]')!
    expect(el.className).not.toMatch(/\bp-3\b/)
  })
})

describe('EmptyState — intent honesty', () => {
  it('error intent is an alert with retry action', () => {
    render(
      <EmptyState
        intent="error"
        title="LOAD FAILED"
        description="Couldn't load scenarios."
        action={<button type="button">Retry</button>}
      />,
    )
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })

  it('awaiting intent is a status region using the awaiting glyph, not error', () => {
    render(<EmptyState intent="awaiting" title="AWAITING DATA" />)
    expect(screen.getByRole('status')).toBeTruthy()
    // default glyph for awaiting is ◌
    expect(screen.getByRole('img').textContent).toBe('◌')
  })

  it('empty intent defaults to a status region', () => {
    render(<EmptyState intent="empty" title="NO FINDINGS" />)
    expect(screen.getByRole('status')).toBeTruthy()
  })
})
