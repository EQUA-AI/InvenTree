import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  mockFaultHistory,
  mockMachineWork,
  occurrenceDetail,
  openFaultHistory
} from './machineWorkFixtures';
import { openPerformance } from './performanceFixtures';

nativeBaseline();
const from = '2026-10-01T00:00:00.000Z';
const to = '2026-10-02T00:00:00.000Z';
const entry = (extra = '') =>
  `/machines/machine/18/performance?history_from=${encodeURIComponent(from)}&history_to=${encodeURIComponent(to)}${extra}`;
for (const scheme of ['light', 'dark'] as const)
  test(`linked selected history and current origin stay separate in ${scheme}`, async ({
    page
  }, info) => {
    await page.route(
      '**/api/assets/machines/18/fault-history/occurrences/901/**',
      (route) => route.fulfill({ json: occurrenceDetail(901) })
    );
    const requests = await openPerformance(page, {
      harness: 's3',
      scheme,
      harnessParams: {
        basename: '/web',
        entry: entry(
          `&occurrence=901&snapshot=${occurrenceDetail(901).snapshots[0].id}`
        )
      }
    });
    await expect(page.getByTestId('linked-evidence')).toContainText(
      'Retained occurrence 901'
    );
    const retained = page.getByTestId('linked-evidence');
    await expect(retained).toContainText('Synthetic SCADA');
    await expect(retained).toContainText(
      occurrenceDetail(901).snapshots[0].content_hash
    );
    await expect(retained).toContainText(
      'Quality and staleness belong to capture time.'
    );
    await retained.screenshot({
      path: info.outputPath(`retained-snapshot-${scheme}.png`),
      animations: 'disabled'
    });
    await expect.poll(() => requests.length).toBe(1);
    expect(requests[0].searchParams.get('from')).toBe(from);
    expect(requests[0].searchParams.get('to')).toBe(to);
    await expect(page.getByTestId('history-provenance')).toContainText(
      'They are not the selected range'
    );
    await expect(page.getByTestId('history-provenance')).toContainText('24 h');
    await expect(page.getByTestId('history-provenance')).not.toContainText(
      'T12:00:00.000Z'
    );
    await expect(page.getByTestId('history-provenance')).toContainText(
      'fast transients may be absent'
    );
    await page.getByTestId('history-provenance').scrollIntoViewIfNeeded();
    expect(
      await page
        .getByTestId('history-provenance')
        .evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
    ).toBe(true);
    await page.getByTestId('history-provenance').screenshot({
      path: info.outputPath(`history-${scheme}.png`),
      animations: 'disabled'
    });
    await page
      .getByRole('button', { name: 'Case span investigation', exact: true })
      .click();
    await page.getByTestId('investigation-slices').screenshot({
      path: info.outputPath(`performance-investigation-${scheme}.png`),
      animations: 'disabled'
    });
    const returnLink = retained.getByRole('link', {
      name: /Retained occurrence 901/
    });
    await expect(returnLink).toHaveAttribute(
      'href',
      /\/web\/machines\/machine\/18\/fault-history\/\?occurrence=901&snapshot=/
    );
    await returnLink.click();
    await expect(page.getByTestId('destination')).toContainText(
      '/machines/machine/18/fault-history/?occurrence=901&snapshot='
    );
  });
test('an invalid linked window issues no history read and has an explicit recovery', async ({
  page
}) => {
  const bad = entry().replace(
    encodeURIComponent(to),
    encodeURIComponent('2026-10-04T00:00:00.000Z')
  );
  const requests = await openPerformance(page, {
    harness: 's3',
    harnessParams: { entry: bad }
  });
  await expect(
    page.getByText('Invalid investigation window', { exact: true })
  ).toBeVisible();
  expect(requests).toHaveLength(0);
  await page
    .getByRole('button', { name: 'Use current range controls', exact: true })
    .click();
  await expect.poll(() => requests.length).toBe(1);
});
test('lost retained reference never substitutes current or other snapshot evidence', async ({
  page
}) => {
  await page.route(
    '**/api/assets/machines/18/fault-history/occurrences/901/**',
    (route) => route.fulfill({ status: 404, json: { detail: 'Unavailable' } })
  );
  await openPerformance(page, {
    harness: 's3',
    harnessParams: { entry: entry('&occurrence=901&snapshot=missing') }
  });
  await expect(
    page.getByText(/Linked retained evidence unavailable or not permitted/)
  ).toBeVisible();
  await expect(page.getByTestId('linked-evidence')).toHaveCount(0);
  await expect(page.getByTestId('history-provenance')).toContainText('Source');
});
test('partial bindings failure names affected charts, comparison and Other coverage', async ({
  page
}, info) => {
  const { NATIVE_SIGNALS } = await import('./performanceFixtures');
  const signals = [
    ...NATIVE_SIGNALS.slice(0, 40),
    ...Array.from({ length: 160 }, (_, i) => ({
      key: `/dex/PUMP1_EXTRA_${i}`,
      name: `Extra ${i}`,
      value: i,
      unit: 'bar'
    }))
  ];
  const requests = await openPerformance(page, {
    harness: 's3',
    signals,
    failSeries: (url) => !!url.searchParams.get('bindings')
  });
  await expect(page.getByTestId('history-provenance')).toContainText(
    'Partial history read'
  );
  await expect(page.getByTestId('history-provenance')).toContainText(
    '1 of 2 bounded batches failed'
  );
  await page.getByRole('button', { name: 'Show all', exact: true }).click();
  await expect(page.locator('[data-section="Electrical"]')).toContainText(
    'History read failed for'
  );
  await expect(page.locator('[data-section="Electrical"]')).not.toContainText(
    'No readings in this window.'
  );
  await expect(page.locator('[data-section="Other signals"]')).toContainText(
    'History read failed; coverage unknown.'
  );
  await expect(
    page.getByText('200 history targets could not be read; coverage unknown', {
      exact: true
    })
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Comparisons and display views', exact: true })
    .click();
  await page
    .getByRole('combobox', { name: 'Comparison channels', exact: true })
    .fill('Shaft speed');
  await page
    .getByRole('option')
    .filter({ hasText: 'Shaft speed' })
    .first()
    .click();
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-comparison-group]')).toContainText(
    'History read failed for Shaft speed'
  );
  await page.locator('[data-section="Electrical"]').screenshot({
    path: info.outputPath('failed-bindings-light.png'),
    animations: 'disabled'
  });
  expect(requests).toHaveLength(2);
});
for (const scheme of ['light', 'dark'] as const)
  test(`a long retained occurrence navigates explicit slices without reading an entire case in ${scheme}`, async ({
    page
  }, info) => {
    await mockMachineWork(page);
    await mockFaultHistory(page);
    const { signalRows, seriesFor } = await import('../performance/fixtures');
    const { NATIVE_SIGNALS } = await import('./performanceFixtures');
    const history: URL[] = [];
    await page.route(
      '**/api/machine-health/machines/18/health/signals/**',
      (route) =>
        route.fulfill({ json: { results: signalRows(NATIVE_SIGNALS) } })
    );
    await page.route(
      '**/api/machine-health/machines/18/health/data-range/**',
      (route) =>
        route.fulfill({
          json: {
            available: true,
            display_shift_seconds: 31536000,
            from: '2026-10-01T00:00:00Z',
            to: '2026-10-04T01:00:00Z'
          }
        })
    );
    await page.route(
      '**/api/machine-health/machines/18/health/series/**',
      (route) => {
        const url = new URL(route.request().url());
        history.push(url);
        return route.fulfill({
          json: {
            ...seriesFor(url, {}, NATIVE_SIGNALS),
            display_shifted: true,
            display_shift_seconds: 31536000
          }
        });
      }
    );
    await page.route(
      '**/api/assets/machines/18/fault-history/occurrences/902/**',
      (route) =>
        route.fulfill({
          json: {
            ...occurrenceDetail(902),
            first_observed_at: '2025-10-01T00:00:00Z',
            last_observed_at: '2025-10-04T01:00:00Z',
            snapshots: [
              {
                ...occurrenceDetail(901).snapshots[0],
                window_start: '2025-10-01T05:00:00Z',
                window_end: '2025-10-01T06:00:00Z'
              }
            ]
          }
        })
    );
    await openFaultHistory(page, {
      query: '?occurrence=902',
      scheme,
      basename: '/web'
    });
    const snapshotLink = page.getByRole('link', {
      name: /Read source history for retained snapshot/
    });
    await expect(snapshotLink).toHaveAttribute(
      'href',
      /\/web\/machines.*history_clock=recorded/
    );
    await snapshotLink.click();
    await expect.poll(() => history.length).toBe(1);
    expect(history[0].searchParams.get('from')).toBe(
      '2026-10-01T05:00:00.000Z'
    );
    expect(history[0].searchParams.get('to')).toBe('2026-10-01T06:00:00.000Z');
    await expect(page.getByTestId('history-provenance')).toContainText(
      'Source timestamps are moved by 31536000 seconds onto the display clock.'
    );
    await page.getByRole('link', { name: /Retained occurrence 902/ }).click();
    await expect(page.getByTestId('occurrence-investigation')).toBeVisible();
    const slices = page.getByTestId('investigation-slices');
    await expect(slices).toContainText('Slice 1 of 4');
    await slices
      .getByRole('button', { name: 'Next slice', exact: true })
      .click();
    await expect(slices).toContainText('Slice 2 of 4');
    await expect(page.getByTestId('occurrence-investigation')).toContainText(
      'does not replace, extend or recapture'
    );
    await slices.scrollIntoViewIfNeeded();
    await page.getByTestId('occurrence-investigation').screenshot({
      path: info.outputPath(`fault-investigation-${scheme}.png`),
      animations: 'disabled'
    });
    expect(history).toHaveLength(1);
    await slices
      .getByRole('button', { name: 'Read this slice', exact: true })
      .click();
    await expect(
      page.getByText('Anything unusual?', { exact: true })
    ).toBeVisible();
    await expect.poll(() => history.length).toBe(2);
    expect(history[1].searchParams.get('from')).toBe(
      '2026-10-02T00:00:00.000Z'
    );
    expect(history[1].searchParams.get('to')).toBe('2026-10-03T00:00:00.000Z');
    await expect(page.getByTestId('linked-evidence')).toContainText(
      'Retained occurrence 902'
    );
  });
test('a recorded link with unknown clock requests no history', async ({
  page
}, info) => {
  const requests = await openPerformance(page, {
    harness: 's3',
    displayShift: null,
    harnessParams: { entry: entry('&history_clock=recorded') }
  });
  await expect(
    page.getByText('Recorded clock unknown', { exact: true })
  ).toBeVisible();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  expect(requests).toHaveLength(0);
  await expect(
    page.getByText('No readings in this window.', { exact: true })
  ).toHaveCount(0);
  await expect(
    page
      .getByText('History has not been returned for this window.', {
        exact: true
      })
      .first()
  ).toBeVisible();
  await page
    .getByRole('alert')
    .filter({ hasText: 'Recorded clock unknown' })
    .screenshot({ path: info.outputPath('clock-unknown-light.png') });
  await page.locator('[data-section="Electrical"]').screenshot({
    path: info.outputPath('clock-unknown-chart-light.png'),
    animations: 'disabled'
  });
});
for (const scheme of ['light', 'dark'] as const)
  test(`failed station key batches retain explicit bay read failures in ${scheme}`, async ({
    page
  }, info) => {
    const { openStation, BAYS } = await import('./stationFixtures');
    const bays = Array.from({ length: 60 }, (_, i) => ({
      ...BAYS[0],
      key: `P${i + 1}`,
      name: `Fixture station / Pump ${i + 1}`,
      machine: i + 18
    }));
    const requests = await openStation(page, {
      scheme,
      bays,
      failSeries: (url) =>
        url.searchParams
          .get('keys')!
          .split(',')
          .includes('/dex/PUMP1_ACTIVE_POWER')
    });
    await expect.poll(() => requests.length).toBe(2);
    await expect(page.getByTestId('history-provenance')).toContainText(
      'Partial history read'
    );
    const card = page
      .locator('.mantine-Paper-root')
      .filter({ has: page.getByText('Active power by bay', { exact: true }) });
    await expect(card).toContainText('History read failed for');
    await expect(card).not.toContainText('No power channel mapped');
    await expect(card).not.toContainText('No history in this window');
    await expect(
      page.locator('tbody tr').filter({
        has: page.getByRole('link', { name: 'Pump 1', exact: true })
      })
    ).toContainText('History read failed');
    await card.screenshot({
      path: info.outputPath(`station-failed-keys-${scheme}.png`),
      animations: 'disabled'
    });
  });
test('station recorded links convert bounds and block an unknown offset', async ({
  page
}) => {
  const { openStation } = await import('./stationFixtures');
  const linkedEntry =
    '/machines/machine/17/performance?history_from=2025-09-20T00:00:00Z&history_to=2025-09-20T01:00:00Z&history_clock=recorded';
  const requests = await openStation(page, {
    entry: linkedEntry,
    displayShift: 31536000
  });
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].searchParams.get('from')).toBe('2026-09-20T00:00:00.000Z');
  expect(requests[0].searchParams.get('to')).toBe('2026-09-20T01:00:00.000Z');
  const unknown = await openStation(page, {
    entry: linkedEntry,
    displayShift: null
  });
  await expect(
    page.getByText('Recorded clock unknown', { exact: true })
  ).toBeVisible();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  expect(unknown).toHaveLength(0);
});
