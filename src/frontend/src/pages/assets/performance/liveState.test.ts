import { expect, it } from 'vitest';
import type { KpiTile } from './KpiStrip';
import { staleTileCount, tileIsStale } from './liveState';

const now = Date.parse('2026-10-22T09:00:00Z');
it('the page counts all stale current tiles rather than judging only its newest reading', () => {
  const tiles = [
    { stale: false, observedAt: now - 20000, freshnessSeconds: 60 },
    { stale: true, observedAt: now + 5000, freshnessSeconds: 60 },
    { stale: false, observedAt: now - 61000, freshnessSeconds: 60 },
    { stale: false, observedAt: null }
  ] as KpiTile[];
  expect(staleTileCount(tiles, now)).toBe(2);
  expect(tileIsStale(tiles[0], now)).toBe(false);
  expect(tileIsStale(tiles[1], now)).toBe(true);
  expect(tileIsStale(tiles[3], now)).toBe(false);
});
it('does not invent an expiry when no freshness budget is configured', () => {
  expect(tileIsStale({ stale: false, observedAt: 1 }, now)).toBe(false);
  expect(
    tileIsStale({ stale: false, observedAt: 1, freshnessSeconds: 0 }, now)
  ).toBe(false);
});
