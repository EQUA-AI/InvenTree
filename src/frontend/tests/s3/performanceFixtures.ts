import { type Page, type Route, expect } from '@playwright/test';
import { mockTheme } from './themeFixtures';

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
  options: {
    signals?: readonly SignalSpec[];
    /** Await the actual initial history response for non-loading scenarios. */
    settleInitialRead?: boolean;
    scheme?: 'light' | 'dark';
    nativeTheme?: boolean;
    compactChrome?: boolean;
    /** The S3 harness: a router whose location the tests can read. */
    harness?: 's0' | 's3';
    /** Extra query parameters for the S3 harness page. */
    harnessParams?: Record<string, string>;
    /** Hold a series answer back, per request (0-based order). */
    delaySeriesMs?: (url: URL, index: number) => number;
    fail?: boolean;
    failSeries?: (url: URL) => boolean;
    displayShift?: number | null;
    mapSeriesResponse?: (
      response: ReturnType<typeof seriesFor>,
      url: URL
    ) => ReturnType<typeof seriesFor>;
  } = {}
) {
  await mockTheme(page);
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
          ...(options.displayShift === null
            ? {}
            : { display_shift_seconds: options.displayShift ?? 0 }),
          from: new Date(NOW - 10 * 86400_000).toISOString(),
          to: new Date(NOW).toISOString()
        }
      })
  );
  await page.route(
    `**/api/machine-health/machines/${MACHINE}/health/series/**`,
    async (route: Route) => {
      const url = new URL(route.request().url());
      const index = requests.length;
      requests.push(url);
      const targetCount = ['bindings', 'keys'].reduce(
        (total, key) =>
          total +
          (url.searchParams.get(key)?.split(',').filter(Boolean).length ?? 0),
        0
      );
      if (options.failSeries?.(url))
        return route.fulfill({
          status: 503,
          json: { detail: 'Synthetic batch unavailable' }
        });
      if (targetCount > 200)
        return route.fulfill({
          status: 400,
          json: { code: 'TOO_MANY_TARGETS' }
        });
      const delay = options.delaySeriesMs?.(url, index) ?? 0;
      if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
      const response = seriesFor(url, { fail: options.fail }, specs);
      const mapped = options.mapSeriesResponse?.(response, url) ?? response;
      // The page may have moved on while this answer was held back.
      return route
        .fulfill({
          json: {
            ...mapped,
            display_shifted: !!options.displayShift,
            display_shift_seconds: options.displayShift ?? 0
          }
        })
        .catch(() => undefined);
    }
  );
  const initialRead = options.settleInitialRead
    ? page
        .waitForResponse(
          (response) =>
            new URL(response.url()).pathname ===
            `/api/machine-health/machines/${MACHINE}/health/series/`,
          { timeout: 60000 }
        )
        .catch((error: Error) => error)
    : undefined;
  if (options.harness === 's3') {
    const query = new URLSearchParams({
      machine: String(MACHINE),
      asset: 'pump',
      name: 'Synthetic pump',
      scheme: options.scheme ?? 'light',
      nativeTheme: String(options.nativeTheme ?? false),
      compactChrome: String(options.compactChrome ?? false),
      ...options.harnessParams
    });
    await page.goto(`/playwright/s3-performance.html?${query}`);
  } else {
    const query = new URLSearchParams({
      surface: 'performance',
      scheme: options.scheme ?? 'light',
      nativeTheme: String(options.nativeTheme ?? false),
      compactChrome: String(options.compactChrome ?? false)
    });
    await page.goto(`/playwright/s0-native.html?${query}`);
  }
  if (initialRead) {
    const response = await initialRead;
    if (response instanceof Error) throw response;
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0', {
      timeout: 60000
    });
  }
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

/** Retained-control tests pause intentional polling before measuring new reads. */
export async function openPausedPerformance(
  page: Page,
  options: Parameters<typeof openPerformance>[1] = {}
) {
  const requests = await openPerformance(page, {
    ...options,
    settleInitialRead: true
  });
  const live = page.getByRole('switch', { name: 'Live', exact: true });
  if (await live.isChecked()) {
    await page
      .locator('label')
      .filter({ hasText: /^Live$/ })
      .click();
    await expect(live).not.toBeChecked();
  }
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0', {
    timeout: 60000
  });
  return requests;
}
