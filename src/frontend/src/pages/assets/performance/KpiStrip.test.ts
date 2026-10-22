import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, i) => text + part + (args[i] ?? ''), '')
}));
import { type KpiTile, KpiTileCard } from './KpiStrip';
const now = Date.parse('2026-10-22T09:00:00Z');
const tile: KpiTile = {
  key: 'pumps-running',
  label: 'Pumps running',
  value: 1,
  denominator: 14,
  unit: '',
  decimals: 0,
  observedAt: now,
  stale: false,
  state: 'unconfigured'
};
const render = (overrides: Partial<KpiTile> = {}) =>
  renderToStaticMarkup(
    createElement(
      MantineProvider,
      {},
      createElement(KpiTileCard, { tile: { ...tile, ...overrides }, now })
    )
  );
it('presents a supplied count with its denominator and no unit/window filler', () => {
  const markup = render();
  expect(markup).toContain('1 of 14');
  expect(markup).not.toContain('unit not recorded');
  expect(markup).not.toContain('Last known');
  expect(markup).not.toContain('height:100%');
  expect(markup).toContain('just now');
});
it('keeps stale visible while the timestamp remains outside its compact badge', () => {
  const markup = render({ stale: true, observedAt: now + 5000 });
  expect(markup).toContain('Stale');
  expect(markup).toContain('0 s old');
  expect(markup).not.toContain('ahead of your clock');
  expect(markup).not.toContain('Last known');
  expect(markup).toContain('data-kpi-observed');
});
it('preserves missing versus a measured zero and its keyboard-accessible reason', () => {
  expect(render({ value: 0 })).toContain('0 of 14');
  const absent = render({
    value: null,
    observedAt: null,
    missingReason: '1 of 14 bays not reporting'
  });
  expect(absent).toContain(
    'aria-label="Pumps running: 1 of 14 bays not reporting"'
  );
  expect(absent).toContain('>—</button>');
  expect(absent).not.toContain('0 of 14');
});
