import { type Page, type Route, expect } from '@playwright/test';

import {
  MACHINE,
  NOW,
  SIGNALS,
  type SignalSpec,
  seriesFor,
  signalRows
} from '../performance/fixtures';

export { SIGNALS, type SignalSpec };

/**
 * The shared fixture plus one tag no catalogue pattern names, so the native
 * Other section (R6 A05.7 "Other fallback") is drawn too.
 */
export const NATIVE_SIGNALS: readonly SignalSpec[] = [
  ...SIGNALS,
  {
    key: '/dex/PUMP1_SPARE_INSTRUMENT_7',
    value: 3.4,
    unit: 'bar',
    name: 'Spare instrument 7'
  }
];

/**
 * The native Performance panel against the shared Cedar-Creek-shaped
 * fixture, with an optional signal list and colour scheme. Only the three
 * Health reads the panel makes are answered; anything else fails the guard.
 */
export async function openPerformance(
  page: Page,
  options: { signals?: readonly SignalSpec[]; scheme?: 'light' | 'dark' } = {}
) {
  const specs = options.signals ?? NATIVE_SIGNALS;
  const requests: URL[] = [];
  await page.route(
    `**/api/machine-health/machines/${MACHINE}/health/signals/**`,
    (route: Route) => {
      const rows = signalRows(specs);
      return route.fulfill({ json: { count: rows.length, results: rows } });
    }
  );
  await page.route(
    `**/api/machine-health/machines/${MACHINE}/health/data-range/**`,
    (route: Route) =>
      route.fulfill({
        json: {
          available: true,
          from: new Date(NOW - 10 * 86400_000).toISOString(),
          to: new Date(NOW).toISOString()
        }
      })
  );
  await page.route(
    `**/api/machine-health/machines/${MACHINE}/health/series/**`,
    (route: Route) => {
      const url = new URL(route.request().url());
      requests.push(url);
      return route.fulfill({ json: seriesFor(url, {}, specs) });
    }
  );
  const query = new URLSearchParams({
    surface: 'performance',
    scheme: options.scheme ?? 'light'
  });
  await page.goto(`/playwright/s0-native.html?${query}`);
  await expect(page.getByText('Anything unusual?')).toBeVisible();
  return requests;
}

/** A signal spec with defaults, for building a machine's tag list. */
export function signal(
  key: string,
  value: number | null,
  unit: string,
  name: string,
  extra: Partial<SignalSpec> = {}
): SignalSpec {
  return { key, value, unit, name, ...extra };
}
