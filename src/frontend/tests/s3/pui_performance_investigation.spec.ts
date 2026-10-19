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
test('partial batch failure keeps available charts and announces the missing coverage', async ({
  page
}) => {
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
    failSeries: (url) => !url.searchParams.get('bindings')
  });
  await expect(page.getByTestId('history-provenance')).toContainText(
    'Partial history read'
  );
  await expect(page.getByTestId('history-provenance')).toContainText(
    '1 of 2 bounded batches failed'
  );
  await page.getByRole('button', { name: 'Show all', exact: true }).click();
  await expect(
    page.locator('[data-section="Electrical"] .recharts-surface').first()
  ).toBeVisible();
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
          json: { available: false, reason: 'Synthetic extent unavailable' }
        })
    );
    await page.route(
      '**/api/machine-health/machines/18/health/series/**',
      (route) => {
        const url = new URL(route.request().url());
        history.push(url);
        return route.fulfill({ json: seriesFor(url, {}, NATIVE_SIGNALS) });
      }
    );
    await page.route(
      '**/api/assets/machines/18/fault-history/occurrences/902/**',
      (route) =>
        route.fulfill({
          json: {
            ...occurrenceDetail(902),
            first_observed_at: '2026-10-01T00:00:00Z',
            last_observed_at: '2026-10-04T01:00:00Z'
          }
        })
    );
    await openFaultHistory(page, { query: '?occurrence=902', scheme });
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
    expect(history).toHaveLength(0);
    await slices
      .getByRole('button', { name: 'Read this slice', exact: true })
      .click();
    await expect(
      page.getByText('Anything unusual?', { exact: true })
    ).toBeVisible();
    await expect.poll(() => history.length).toBe(1);
    expect(history[0].searchParams.get('from')).toBe(
      '2026-10-02T00:00:00.000Z'
    );
    expect(history[0].searchParams.get('to')).toBe('2026-10-03T00:00:00.000Z');
    await expect(page.getByTestId('linked-evidence')).toContainText(
      'Retained occurrence 902'
    );
  });
