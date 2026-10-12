import { describe, expect, it } from 'vitest';

import type { DashboardWidgetProps } from './DashboardWidget';
import {
  DEFAULT_WIDGET_HEIGHT,
  DEFAULT_WIDGET_WIDTH,
  addWidgetToLayouts,
  newWidgetLayoutItem
} from './dashboardLayoutUtils';

function widget(
  extra: Partial<DashboardWidgetProps> = {}
): DashboardWidgetProps {
  return {
    label: 'machine-alarms',
    title: 'Machine Alarms',
    description: '',
    minWidth: 4,
    minHeight: 3,
    render: () => null as any,
    ...extra
  };
}

describe('newWidgetLayoutItem', () => {
  it('gives a new widget the default size, not its minimum', () => {
    const item = newWidgetLayoutItem(widget(), [], 12);
    expect(item.w).toBe(DEFAULT_WIDGET_WIDTH);
    expect(item.h).toBe(DEFAULT_WIDGET_HEIGHT);
    expect(item.minW).toBe(4);
    expect(item.minH).toBe(3);
  });

  it('never goes below the widget minimum', () => {
    const item = newWidgetLayoutItem(
      widget({ minWidth: 8, minHeight: 7 }),
      [],
      12
    );
    expect(item.w).toBe(8);
    expect(item.h).toBe(7);
  });

  it('honours a declared default size', () => {
    const item = newWidgetLayoutItem(
      widget({ defaultWidth: 12, defaultHeight: 6 }),
      [],
      12
    );
    expect(item.w).toBe(12);
    expect(item.h).toBe(6);
  });

  it('never exceeds the grid width', () => {
    const item = newWidgetLayoutItem(widget({ minWidth: 5 }), [], 2);
    expect(item.w).toBe(2);
    expect(item.minW).toBe(2);
  });

  it('is placed below the existing widgets', () => {
    const existing = [
      { i: 'a', x: 0, y: 0, w: 6, h: 4 },
      { i: 'b', x: 6, y: 2, w: 6, h: 8 }
    ];
    const item = newWidgetLayoutItem(widget(), existing, 12);
    expect(item.x).toBe(0);
    expect(item.y).toBe(10);
  });
});

describe('addWidgetToLayouts', () => {
  it('creates an lg layout when none is stored', () => {
    const layouts = addWidgetToLayouts({}, widget());
    expect(Object.keys(layouts)).toEqual(['lg']);
    expect(layouts.lg[0].i).toBe('machine-alarms');
    expect(layouts.lg[0].w).toBe(DEFAULT_WIDGET_WIDTH);
  });

  it('adds to every stored breakpoint, clamped to its columns', () => {
    const layouts = addWidgetToLayouts(
      { lg: [{ i: 'a', x: 0, y: 0, w: 12, h: 4 }], xs: [] },
      widget()
    );
    expect(layouts.lg).toHaveLength(2);
    expect(layouts.lg[1].y).toBe(4);
    expect(layouts.xs[0].w).toBe(4);
  });

  it('keeps an existing entry for the widget', () => {
    const existing = { i: 'machine-alarms', x: 3, y: 1, w: 9, h: 9 };
    const layouts = addWidgetToLayouts({ lg: [existing] }, widget());
    expect(layouts.lg).toEqual([existing]);
  });
});
