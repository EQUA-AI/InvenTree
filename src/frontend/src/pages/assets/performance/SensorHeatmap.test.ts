import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, index) => text + part + (args[index] ?? ''), '')
}));
import { SensorHeatmap } from './SensorHeatmap';
import type { HeatmapMatrix } from './series';

const matrix = (cells: number[], configured = true): HeatmapMatrix => ({
  columns: cells.map((_, index) => ({
    start: index * 5000,
    end: (index + 1) * 5000
  })),
  min: Math.min(...cells),
  max: Math.max(...cells),
  rows: [
    {
      key: 'winding',
      label: 'Winding 1',
      cells,
      counts: cells.map(() => 1),
      last: cells.at(-1)!,
      limits: configured
        ? {
            normal_min: null,
            normal_max: null,
            warn_min: null,
            warn_max: 125,
            critical_min: null,
            critical_max: 145
          }
        : null
    }
  ]
});
const render = (cells: number[], configured = true) =>
  renderToStaticMarkup(
    createElement(
      MantineProvider,
      {},
      createElement(SensorHeatmap, {
        matrix: matrix(cells, configured),
        unit: 'degC',
        decimals: 1,
        windowSeconds: 3600
      })
    )
  );

it('keeps the whole healthy spread neutral instead of stretching it to an outlier colour scale', () => {
  const markup = render([76.4, 80, 83.6]);
  const fills = [
    ...markup.matchAll(/<rect[^>]*fill="([^"]+)"[^>]*data-heatmap-cell/g)
  ].map((match) => match[1]);
  expect(fills).toHaveLength(3);
  expect(new Set(fills).size).toBe(1);
  expect(markup.match(/data-limit-state="normal"/g)).toHaveLength(3);
  expect(markup).not.toContain('Scale spans');
});
it('uses configured strict boundaries with an additional warning/critical shape cue', () => {
  const markup = render([125, 126, 146]);
  expect(markup).toContain('data-limit-state="normal"');
  expect(markup).toContain('data-limit-state="warning"');
  expect(markup).toContain('data-limit-state="critical"');
  expect(markup).toContain('M0,-3 L3,3 L-3,3 Z');
  expect(markup).toContain('M-2,-2 L2,2 M-2,2 L2,-2');
});
it('does not assign an alarm colour where no limits are configured', () => {
  const markup = render([1, 999], false);
  expect(markup.match(/data-limit-state="unconfigured"/g)).toHaveLength(2);
  expect(markup).not.toContain('mantine-color-red');
  expect(markup).not.toContain('mantine-color-yellow');
});
