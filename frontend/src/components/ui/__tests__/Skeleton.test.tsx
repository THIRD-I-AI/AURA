import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Skeleton, KPISkeleton, TableSkeleton, ChartSkeleton, CardSkeleton } from '../Skeleton';

describe('Skeleton', () => {
  it('renders a hidden placeholder', () => {
    const { container } = render(<Skeleton />);
    const el = container.firstElementChild!;
    expect(el.getAttribute('aria-hidden')).toBe('true');
  });

  it('applies custom width and height', () => {
    const { container } = render(<Skeleton width="200px" height="2rem" />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.style.width).toBe('200px');
    expect(el.style.height).toBe('2rem');
  });
});

describe('Composite skeletons', () => {
  it('KPISkeleton renders 4 cards', () => {
    const { container } = render(<KPISkeleton />);
    // 4 grid children, each with 3 Skeleton shimmer divs
    const gridChildren = container.firstElementChild!.children;
    expect(gridChildren.length).toBe(4);
  });

  it('TableSkeleton renders header + rows', () => {
    const { container } = render(<TableSkeleton rows={3} cols={2} />);
    // 1 header row + 3 data rows = 4 children
    const rows = container.firstElementChild!.children;
    expect(rows.length).toBe(4);
  });

  it('ChartSkeleton renders without error', () => {
    const { container } = render(<ChartSkeleton height={300} />);
    expect(container.firstElementChild).toBeTruthy();
  });

  it('CardSkeleton renders correct number of lines', () => {
    const { container } = render(<CardSkeleton lines={5} />);
    // 1 title skeleton + 5 line skeletons = 6 children
    const children = container.firstElementChild!.children;
    expect(children.length).toBe(6);
  });
});
