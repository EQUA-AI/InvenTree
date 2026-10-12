import type { Layout } from 'react-grid-layout';

import type { DashboardWidgetProps } from './DashboardWidget';

/** Grid breakpoints (px) and the number of columns at each. */
export const DASHBOARD_BREAKPOINTS = {
  lg: 1200,
  md: 996,
  sm: 768,
  xs: 480,
  xxs: 0
};

export const DASHBOARD_COLS: Record<string, number> = {
  lg: 12,
  md: 10,
  sm: 6,
  xs: 4,
  xxs: 2
};

/** Size a newly added widget gets when it does not declare its own. */
export const DEFAULT_WIDGET_WIDTH = 6;
export const DEFAULT_WIDGET_HEIGHT = 4;

/**
 * Layout entry for a widget being added to one breakpoint's layout.
 *
 * Without an explicit entry, react-grid-layout places a new child at 1x1 and
 * the dashboard then only raises it to the widget's minimum size, so every
 * new widget arrived as a very small tile. This gives it a usable default
 * size (never below its minimum, never wider than the grid) and puts it
 * below the existing widgets.
 */
export function newWidgetLayoutItem(
  widget: DashboardWidgetProps,
  existing: Layout[],
  cols: number
): Layout {
  const minW = Math.min(widget.minWidth ?? 1, cols);
  const minH = widget.minHeight ?? 2;
  const w = Math.min(
    Math.max(widget.defaultWidth ?? DEFAULT_WIDGET_WIDTH, minW),
    cols
  );
  const h = Math.max(widget.defaultHeight ?? DEFAULT_WIDGET_HEIGHT, minH);
  const bottom = existing.reduce(
    (max, item) => Math.max(max, (item.y ?? 0) + (item.h ?? 0)),
    0
  );

  return { i: widget.label, x: 0, y: bottom, w, h, minW, minH };
}

/**
 * Add a widget to every stored breakpoint layout (or to ``lg`` when none is
 * stored yet; other breakpoints are derived from it by the grid). A widget
 * that already has an entry keeps it.
 */
export function addWidgetToLayouts(
  layouts: Record<string, Layout[]>,
  widget: DashboardWidgetProps
): Record<string, Layout[]> {
  const keys = Object.keys(layouts).length > 0 ? Object.keys(layouts) : ['lg'];
  const result: Record<string, Layout[]> = { ...layouts };

  keys.forEach((key) => {
    const existing = layouts[key] ?? [];
    if (existing.some((item) => item.i === widget.label)) {
      return;
    }
    const cols = DASHBOARD_COLS[key] ?? DASHBOARD_COLS.lg;
    result[key] = [...existing, newWidgetLayoutItem(widget, existing, cols)];
  });

  return result;
}
