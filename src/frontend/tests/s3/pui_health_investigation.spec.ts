import { type Request, expect, test } from '@playwright/test';
import type { HealthEvidenceSnapshot } from '../../lib/types/MachineHealth';
import { nativeBaseline } from '../s0/guards';
import {
  HEALTH_SIGNALS,
  NOW,
  anomaly,
  openHealth,
  signalTable,
  trendCard,
  trendFor
} from './healthFixtures';
import { occurrenceDetail } from './machineWorkFixtures';

nativeBaseline();
test.use({ timezoneId: 'UTC' });
const from = '2026-10-17T06:00:00.000Z';
const to = '2026-10-17T06:10:00.000Z';
const search = (start = from, end = to, extra = '') =>
  `?history_from=${encodeURIComponent(start)}&history_to=${encodeURIComponent(end)}${extra}`;
const specs = HEALTH_SIGNALS.slice(0, 2);
const snapshot: HealthEvidenceSnapshot = {
  id: '9ae91736-38b8-4d7e-aafe-d8f64e2263ed',
  machine: 18,
  binding: 201,
  source: 1,
  anomaly: 901,
  signal_label: 'Retained flow',
  unit: 'm3/h',
  captured_at: '2025-10-17T06:11:00Z',
  window_start: '2025-10-17T06:00:00Z',
  window_end: '2025-10-17T06:10:00Z',
  samples: [{ observed_at: '2025-10-17T06:00:00Z', value: 380 }],
  statistics: {},
  quality: 'bad',
  stale: true,
  reason: 'anomaly_repair',
  source_references: { source: 'Original historian' },
  content_hash: 'retained-original-hash',
  system_actor: 'detector'
};

for (const scheme of ['light', 'dark'] as const) {
  test(`Health fixed window, native evidence and expansion preserve origins (${scheme})`, async ({
    page
  }, info) => {
    const occurrence = occurrenceDetail(901);
    await page.route(
      '**/api/assets/machines/18/fault-history/occurrences/901/**',
      (route) => route.fulfill({ json: occurrence })
    );
    const requests = await openHealth(page, {
      recordedTelemetryAsLive: false,
      scheme,
      signals: specs,
      entrySearch: search(
        from,
        to,
        `&occurrence=901&snapshot=${occurrence.snapshots[0].id}`
      )
    });
    await expect.poll(() => requests.chart.length).toBe(1);
    expect(requests.chart[0].searchParams.get('from')).toBe(from);
    expect(requests.chart[0].searchParams.get('to')).toBe(to);
    expect(requests.chart[0].searchParams.get('max_samples')).toBe('4320');
    expect(requests.batch).toHaveLength(1);
    expect(requests.batch[0].searchParams.get('to')).toBe(
      new Date(NOW).toISOString()
    );
    await expect(
      signalTable(page).getByRole('cell', { name: '812.5 cusec', exact: true })
    ).toBeVisible();
    const card = trendCard(page);
    expect(
      await card.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
    ).toBe(true);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1
      )
    ).toBe(true);
    await expect(card).toContainText(
      'Current values and recent row trends differ from selected history'
    );
    await card
      .getByRole('button', { name: 'Show read details', exact: true })
      .click();
    await expect(card.getByText(/Measured interval: 5 s/)).toBeVisible();
    await expect(card).toContainText('Most samples per read: 4320');
    await expect(card).toContainText('Fast transients may be absent');
    await expect(card).toContainText('Gaps longer than 15 seconds');
    const retained = card.getByTestId('linked-evidence');
    await expect(retained).toContainText(occurrence.snapshots[0].content_hash);
    await expect(retained.getByRole('link')).toHaveAttribute(
      'href',
      /occurrence=901&snapshot=/
    );
    await card.screenshot({
      path: info.outputPath(`health-window-${scheme}.png`)
    });
    await card
      .getByRole('button', { name: 'Inspect selected signal', exact: true })
      .click();
    const evidence = page.getByRole('region', {
      name: 'Signal evidence: Discharge flow'
    });
    await expect(evidence).toContainText(
      'Chart origin: selected fixed source window'
    );
    await expect(evidence).toContainText('Latest recorded reading');
    await evidence.screenshot({
      path: info.outputPath(`health-window-evidence-${scheme}.png`)
    });
    await evidence
      .getByRole('button', { name: 'Close signal evidence' })
      .click();
    const count = requests.chart.length;
    await card
      .getByRole('button', { name: 'Expand chart', exact: true })
      .click();
    const dialog = page.getByRole('dialog', { name: 'Expanded signal trend' });
    await expect(dialog.getByText(/^Selected window:/)).toContainText(
      '17 Oct 2026'
    );
    await expect(dialog.locator('.recharts-area-curve')).toHaveAttribute(
      'stroke',
      /blue-6/
    );
    await dialog.screenshot({
      path: info.outputPath(`health-window-expanded-${scheme}.png`)
    });
    await page.keyboard.press('Escape');
    await expect(
      page.getByRole('button', { name: 'Expand chart', exact: true })
    ).toBeFocused();
    expect(requests.chart).toHaveLength(count);
  });
}

test('a 13-hour linked case needs explicit contiguous six-hour reads without prefetch', async ({
  page
}, info) => {
  const end = '2026-10-17T19:00:00.000Z';
  const requests = await openHealth(page, {
    recordedTelemetryAsLive: false,
    signals: [{ ...specs[0], trend: 'empty' }],
    entrySearch: search(from, end)
  });
  await expect(trendCard(page)).toContainText('Choose a six-hour slice');
  expect(requests.chart).toHaveLength(0);
  const slices = page.getByTestId('investigation-slices');
  await expect(slices).toContainText('Slice 1 of 3');
  await expect(slices).toContainText('at most 6 h each');
  await slices.getByRole('button', { name: 'Next slice' }).click();
  await expect(slices).toContainText('Slice 2 of 3');
  expect(requests.chart).toHaveLength(0);
  await slices.screenshot({
    path: info.outputPath('health-six-hour-slices.png')
  });
  await slices.getByRole('button', { name: 'Read this slice' }).click();
  await expect.poll(() => requests.chart.length).toBe(1);
  expect(requests.chart[0].searchParams.get('from')).toBe(
    '2026-10-17T12:00:00.000Z'
  );
  expect(requests.chart[0].searchParams.get('to')).toBe(
    '2026-10-17T18:00:00.000Z'
  );
  await expect(
    trendCard(page).getByRole('combobox', { name: 'Range', exact: true })
  ).toHaveValue('Linked investigation window');
  await slices.getByRole('button', { name: 'Next slice' }).click();
  await expect(slices).toContainText('Slice 3 of 3');
  expect(requests.chart).toHaveLength(1);
  await slices.getByRole('button', { name: 'Read this slice' }).click();
  await expect.poll(() => requests.chart.length).toBe(2);
  expect(requests.chart[1].searchParams.get('to')).toBe(end);
  expect(requests.chart[1].searchParams.get('from')).toBe(
    '2026-10-17T18:00:00.000Z'
  );
  expect(requests.batch).toHaveLength(1);
  await page.getByRole('button', { name: 'Expand chart', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Expanded signal trend' });
  await expect(dialog.getByTestId('investigation-slices')).toContainText(
    'Slice 3 of 3'
  );
  await dialog.screenshot({
    path: info.outputPath('health-slices-expanded.png')
  });
  await page.keyboard.press('Escape');
  await expect(
    page.getByRole('button', { name: 'Expand chart', exact: true })
  ).toBeFocused();
  await expect(page.getByTestId('investigation-slices')).toContainText(
    'Slice 3 of 3'
  );
  expect(requests.chart).toHaveLength(2);
});

test('recorded snapshot links convert once with a fresh offset and keep the exact references', async ({
  page
}, info) => {
  await page.route(
    '**/api/assets/machines/18/fault-history/occurrences/901/**',
    (route) =>
      route.fulfill({
        json: {
          ...occurrenceDetail(901),
          snapshots: [
            {
              ...occurrenceDetail(901).snapshots[0],
              id: snapshot.id,
              content_hash: snapshot.content_hash,
              source_name: 'Original historian',
              window_start: snapshot.window_start,
              window_end: snapshot.window_end,
              captured_at: snapshot.captured_at,
              quality: snapshot.quality,
              stale: snapshot.stale,
              samples: snapshot.samples,
              statistics: snapshot.statistics,
              system_actor: snapshot.system_actor,
              captured_by_name: null
            }
          ]
        }
      })
  );
  const requests = await openHealth(page, {
    recordedTelemetryAsLive: false,
    signals: specs,
    snapshots: [snapshot],
    dataRange: { display_shift_seconds: 31536000 },
    chartOverrides: {
      recorded_telemetry_as_live: false,
      display_shifted: true,
      display_shift_seconds: 31536000
    }
  });
  await signalTable(page)
    .getByRole('button', {
      name: 'Inspect signal: Discharge flow',
      exact: true
    })
    .click();
  await page
    .getByRole('link', {
      name: 'Investigate snapshot source window',
      exact: true
    })
    .click();
  await expect
    .poll(() => requests.chart.at(-1)?.searchParams.get('from'))
    .toBe(from);
  expect(requests.chart.at(-1)?.searchParams.get('to')).toBe(to);
  await expect(page.getByTestId('linked-evidence')).toContainText(
    snapshot.content_hash
  );
  await expect(trendCard(page)).toContainText('365.0 days later');
  await expect(
    page.getByRole('region', { name: 'Retained evidence', exact: true })
  ).toContainText('Original historian');
  await expect(
    page.getByRole('region', { name: 'Retained evidence', exact: true })
  ).toContainText('380');
  await trendCard(page).screenshot({
    path: info.outputPath('health-recorded-clock.png')
  });
  await trendCard(page)
    .getByRole('combobox', { name: 'Range', exact: true })
    .click();
  await page.getByRole('option', { name: '5 minutes', exact: true }).click();
  await expect
    .poll(() => requests.chart.at(-1)?.searchParams.get('to'))
    .toBe(new Date(NOW).toISOString());
  await expect(
    page.getByTestId('linked-evidence').getByRole('link')
  ).toHaveAttribute(
    'href',
    new RegExp(`occurrence=901&snapshot=${snapshot.id}`)
  );
});

for (const mode of ['missing', 'failed'] as const)
  test(`unknown recorded clock (${mode}) reads no selected history`, async ({
    page
  }) => {
    const requests = await openHealth(page, {
      recordedTelemetryAsLive: false,
      signals: specs,
      entrySearch: search(
        snapshot.window_start,
        snapshot.window_end,
        '&history_clock=recorded'
      ),
      dataRange: { display_shift_seconds: null },
      dataRangeStatus: mode === 'failed' ? 503 : 200
    });
    await expect(trendCard(page)).toContainText('Recorded clock unknown');
    expect(requests.chart).toHaveLength(0);
    await expect(trendCard(page)).not.toContainText(
      'No samples in this window'
    );
    await page.route(
      '**/api/machine-health/machines/18/health/data-range/**',
      (route) =>
        route.fulfill({
          json: { available: true, from, to, display_shift_seconds: 31536000 }
        })
    );
    await page.getByRole('button', { name: 'Retry source extent' }).click();
    await expect.poll(() => requests.chart.length).toBe(1);
    expect(requests.chart[0].searchParams.get('from')).toBe(from);
  });

test('malformed and over-24-hour links fail closed with native recovery', async ({
  page
}) => {
  await page.route(
    '**/api/assets/machines/18/fault-history/occurrences/901/**',
    (route) => route.fulfill({ status: 403, json: { detail: 'Denied' } })
  );
  const requests = await openHealth(page, {
    recordedTelemetryAsLive: false,
    signals: specs,
    entrySearch: search(from, '2026-10-19T06:00:00Z', '&occurrence=901')
  });
  await expect(trendCard(page)).toContainText('Invalid investigation window');
  expect(requests.chart).toHaveLength(0);
  await page
    .getByRole('button', { name: 'Use current range controls' })
    .click();
  await expect.poll(() => requests.chart.length).toBe(1);
  expect(requests.chart[0].searchParams.get('to')).toBe(
    new Date(NOW).toISOString()
  );
});

test('permission loss never substitutes retained evidence or reports an empty read', async ({
  page
}) => {
  await page.route(
    '**/api/assets/machines/18/fault-history/occurrences/901/**',
    (route) => route.fulfill({ status: 403, json: { detail: 'Denied' } })
  );
  await openHealth(page, {
    recordedTelemetryAsLive: false,
    signals: specs,
    entrySearch: search(from, to, `&occurrence=901&snapshot=${snapshot.id}`),
    snapshotsStatus: 403,
    chartStatus: 403
  });
  await expect(trendCard(page)).toContainText(
    'Linked retained evidence unavailable or not permitted'
  );
  await expect(trendCard(page)).toContainText('History access denied');
  await expect(trendCard(page)).not.toContainText('No samples in this window');
  await expect(
    page.getByRole('region', { name: 'Retained evidence', exact: true })
  ).toContainText('Retained snapshot unavailable');
  await expect(trendCard(page).locator('.recharts-area-curve')).toHaveCount(0);
});

test('a capped read and unavailable cadence keep partial coverage visible', async ({
  page
}, info) => {
  await openHealth(page, {
    recordedTelemetryAsLive: false,
    signals: specs,
    entrySearch: search(),
    cadence: false,
    chartOverrides: { truncated: true }
  });
  await expect(trendCard(page)).toContainText('This window was truncated');
  await expect(trendCard(page)).toContainText(
    'most recent part of this window is not shown'
  );
  await expect(trendCard(page)).toContainText('Measured interval unavailable');
  await expect(trendCard(page)).toContainText('Expected interval not supplied');
  await expect(trendCard(page)).toContainText(
    'silences between readings are not marked'
  );
  await trendCard(page).screenshot({
    path: info.outputPath('health-partial-history.png')
  });
});

test('point anomalies and snapshots get no inferred duration; occurrence links retain recorded bounds', async ({
  page
}) => {
  const point = { ...snapshot, window_end: snapshot.window_start };
  const requests = await openHealth(page, {
    recordedTelemetryAsLive: false,
    signals: specs,
    snapshots: [point],
    anomalies: [
      anomaly({
        pk: 901,
        signals: [
          {
            binding_id: 201,
            display_name: specs[0].name,
            unit: specs[0].unit,
            signal_kind: 'flow'
          }
        ],
        first_observed_at: from,
        last_observed_at: to
      }),
      anomaly({
        pk: 902,
        title: 'Point observation without duration',
        signals: [
          {
            binding_id: 201,
            display_name: specs[0].name,
            unit: specs[0].unit,
            signal_kind: 'flow'
          }
        ],
        first_observed_at: from,
        last_observed_at: from
      })
    ],
    summary: {
      active_anomaly_count: 2,
      anomaly_counts: { critical: 0, warning: 2, info: 0 }
    },
    dataRange: { display_shift_seconds: 0 }
  });
  await signalTable(page)
    .getByRole('button', {
      name: 'Inspect signal: Discharge flow',
      exact: true
    })
    .click();
  const evidence = page.getByRole('region', {
    name: 'Signal evidence: Discharge flow'
  });
  await expect(
    evidence.getByText(
      'No increasing source window recorded; no event duration is inferred.',
      { exact: true }
    )
  ).toHaveCount(2);
  await expect(
    evidence.getByRole('link', { name: 'Investigate snapshot source window' })
  ).toHaveCount(0);
  const link = evidence.getByRole('link', {
    name: 'Investigate occurrence source window'
  });
  await expect(link).toHaveAttribute('href', /history_clock=recorded/);
  await page.route(
    '**/api/assets/machines/18/fault-history/occurrences/901/**',
    (route) => route.fulfill({ json: occurrenceDetail(901) })
  );
  await link.click();
  await expect
    .poll(() => requests.chart.at(-1)?.searchParams.get('from'))
    .toBe(from);
  expect(requests.chart.at(-1)?.searchParams.get('to')).toBe(to);
});

for (const anomalyShift of [31536000, 0]) {
  const recordedFrom = new Date(
    Date.parse(from) - anomalyShift * 1000
  ).toISOString();
  const recordedTo = new Date(
    Date.parse(to) - anomalyShift * 1000
  ).toISOString();
  test(`occurrence stays on its recorded window as the binding clock advances (anomaly offset ${anomalyShift})`, async ({
    page
  }, info) => {
    let bindingShift = 31536000;
    const sourceReads: { from: string; to: string }[] = [];
    const requests = await openHealth(page, {
      recordedTelemetryAsLive: false,
      signals: specs,
      dataRange: { display_shift_seconds: bindingShift },
      anomalies: [
        anomaly({
          pk: 901,
          source: anomalyShift ? 1 : null,
          first_observed_at: from,
          last_observed_at: to,
          display_shift_seconds: anomalyShift,
          signals: [
            {
              binding_id: 201,
              display_name: specs[0].name,
              unit: specs[0].unit,
              signal_kind: 'flow'
            }
          ]
        })
      ],
      chartHandler: async (route, url) => {
        const start = Date.parse(url.searchParams.get('from')!);
        const end = Date.parse(url.searchParams.get('to')!);
        // Model read_trend's conversion with the offset at read time, rather
        // than merely asserting that old display bounds were sent unchanged.
        sourceReads.push({
          from: new Date(start - bindingShift * 1000).toISOString(),
          to: new Date(end - bindingShift * 1000).toISOString()
        });
        await route.fulfill({
          json: {
            ...trendFor(specs[0], start, end),
            display_shifted: true,
            display_shift_seconds: bindingShift
          }
        });
      }
    });
    await page.route(
      '**/api/machine-health/machines/18/health/data-range/**',
      (route) =>
        route.fulfill({
          json: {
            available: true,
            from,
            to,
            display_shift_seconds: bindingShift
          }
        })
    );
    await page.route(
      '**/api/assets/machines/18/fault-history/occurrences/901/**',
      (route) => route.fulfill({ json: occurrenceDetail(901) })
    );
    await signalTable(page)
      .getByRole('button', {
        name: 'Inspect signal: Discharge flow',
        exact: true
      })
      .click();
    const link = page.getByRole('link', {
      name: 'Investigate occurrence source window',
      exact: true
    });
    const href = await link.getAttribute('href');
    const params = new URL(href!, 'http://fixture.invalid').searchParams;
    expect(params.get('history_clock')).toBe('recorded');
    expect(params.get('history_from')).toBe(recordedFrom);
    expect(params.get('history_to')).toBe(recordedTo);
    expect(params.get('binding')).toBe('201');
    expect(params.get('occurrence')).toBe('901');
    // The anomaly has been fetched. Advance the server's moving binding clock
    // by 20 min before reading the occurrence, including the null-source case.
    bindingShift += 1200;
    await link.click();
    await expect
      .poll(() => sourceReads.at(-1))
      .toEqual({ from: recordedFrom, to: recordedTo });
    await expect(page.getByTestId('linked-evidence')).toContainText(
      'Retained occurrence 901'
    );
    expect(requests.chart.at(-1)?.searchParams.get('from')).toBe(
      new Date(Date.parse(recordedFrom) + bindingShift * 1000).toISOString()
    );
    await expect(trendCard(page)).toContainText(
      'Chart origin: selected fixed source window'
    );
    await expect(page.getByTestId('linked-evidence')).toContainText(
      'Retained occurrence 901'
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1
      )
    ).toBe(true);
    await trendCard(page).screenshot({
      path: info.outputPath(`health-occurrence-read-${anomalyShift}.png`)
    });
  });

  test(`a recorded occurrence URL reopened a day later keeps its source window (anomaly offset ${anomalyShift})`, async ({
    page
  }, info) => {
    const previousShift = 31537200;
    const bindingShift = previousShift + 86400;
    const sourceReads: { from: string; to: string }[] = [];
    await page.route(
      '**/api/assets/machines/18/fault-history/occurrences/901/**',
      (route) => route.fulfill({ json: occurrenceDetail(901) })
    );
    // Open the canonical recorded URL checked by the link test in an independent
    // browser context, like a shared/bookmarked URL rather than fixture memory.
    const requests = await openHealth(page, {
      recordedTelemetryAsLive: false,
      signals: specs,
      entrySearch: search(
        recordedFrom,
        recordedTo,
        '&history_clock=recorded&binding=201&occurrence=901'
      ),
      dataRange: { display_shift_seconds: bindingShift },
      chartHandler: async (route, url) => {
        const start = Date.parse(url.searchParams.get('from')!);
        const end = Date.parse(url.searchParams.get('to')!);
        sourceReads.push({
          from: new Date(start - bindingShift * 1000).toISOString(),
          to: new Date(end - bindingShift * 1000).toISOString()
        });
        await route.fulfill({
          json: {
            ...trendFor(specs[0], start, end),
            display_shifted: true,
            display_shift_seconds: bindingShift
          }
        });
      }
    });
    await expect
      .poll(() => sourceReads.at(-1))
      .toEqual({ from: recordedFrom, to: recordedTo });
    const displayed = requests.chart.at(-1)!;
    expect(displayed.searchParams.get('from')).not.toBe(
      new Date(Date.parse(recordedFrom) + previousShift * 1000).toISOString()
    );
    expect(displayed.searchParams.get('from')).toBe(
      new Date(Date.parse(recordedFrom) + bindingShift * 1000).toISOString()
    );
    expect(displayed.searchParams.get('to')).toBe(
      new Date(Date.parse(recordedTo) + bindingShift * 1000).toISOString()
    );
    await expect(trendCard(page)).toContainText(
      'Chart origin: selected fixed source window'
    );
    await expect(page.getByTestId('linked-evidence')).toContainText(
      'Retained occurrence 901'
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1
      )
    ).toBe(true);
    await trendCard(page).screenshot({
      path: info.outputPath(`health-stable-occurrence-${anomalyShift}.png`)
    });
  });
}

test('an occurrence without its own clock offset has no source-window link', async ({
  page
}) => {
  await openHealth(page, {
    recordedTelemetryAsLive: false,
    signals: specs,
    anomalies: [
      anomaly({
        source: null,
        display_shift_seconds: undefined,
        signals: [
          {
            binding_id: 201,
            display_name: specs[0].name,
            unit: specs[0].unit,
            signal_kind: 'flow'
          }
        ]
      })
    ]
  });
  await signalTable(page)
    .getByRole('button', {
      name: 'Inspect signal: Discharge flow',
      exact: true
    })
    .click();
  const evidence = page.getByRole('region', {
    name: 'Signal evidence: Discharge flow'
  });
  await expect(evidence).toContainText(
    'Occurrence clock offset unavailable; source window cannot be linked.'
  );
  await expect(
    evidence.getByRole('link', { name: 'Investigate occurrence source window' })
  ).toHaveCount(0);
});

for (const scheme of ['light', 'dark'] as const)
  test(`a manual 13-hour case keeps its span and slice position across reads (${scheme})`, async ({
    page
  }, info) => {
    let releaseExtent = () => {};
    const dataRangeReady = new Promise<void>((resolve) => {
      releaseExtent = resolve;
    });
    const requests = await openHealth(page, {
      recordedTelemetryAsLive: false,
      scheme,
      signals: [{ ...specs[0], trend: 'empty' }],
      dataRangeReady
    });
    await expect.poll(() => requests.chart.length).toBe(1);
    await page
      .getByRole('button', { name: 'Case span investigation', exact: true })
      .click();
    const slices = page.getByTestId('investigation-slices');
    const setTime = async (label: string, hour: string) => {
      const trigger = slices.getByLabel(`${label} (UTC)`, { exact: true });
      await trigger.click();
      const dropdownId = await trigger.getAttribute('aria-controls');
      expect(dropdownId).toBeTruthy();
      const picker = page.locator(`[id="${dropdownId}"]`);
      await picker
        .getByRole('button', { name: '17 October 2026', exact: true })
        .click();
      const time = picker.getByRole('spinbutton');
      await time.nth(0).fill(hour);
      await time.nth(1).fill('00');
      await time.nth(1).press('Enter');
    };
    await setTime('Case span from', '06');
    await setTime('Case span to', '19');
    await slices
      .getByRole('button', { name: 'Set case span', exact: true })
      .click();
    await expect(slices).toContainText('Slice 1 of 3');
    expect(requests.chart).toHaveLength(1);
    await slices.getByRole('button', { name: 'Read this slice' }).click();
    await expect.poll(() => requests.chart.length).toBe(2);
    expect(requests.chart[1].searchParams.get('from')).toBe(from);
    expect(requests.chart[1].searchParams.get('to')).toBe(
      '2026-10-17T12:00:00.000Z'
    );
    await expect(slices).toContainText('Slice 1 of 3');
    const extentArrived = page.waitForResponse((response) =>
      response.url().includes('/health/data-range/')
    );
    releaseExtent();
    await (await extentArrived).finished();
    await expect(trendCard(page).getByText(/^Data to /)).toBeVisible();
    const range = trendCard(page).getByRole('combobox', {
      name: 'Range',
      exact: true
    });
    await expect(range).toHaveValue('Selected case slice');
    await range.click();
    await page
      .getByRole('option', { name: 'Selected case slice', exact: true })
      .click();
    await expect(range).toHaveValue('Selected case slice');
    await expect(slices).toContainText('Slice 1 of 3');
    expect(requests.chart).toHaveLength(2);
    await slices.getByRole('button', { name: 'Next slice' }).click();
    await expect(slices).toContainText('Slice 2 of 3');
    expect(requests.chart).toHaveLength(2);
    await slices.getByRole('button', { name: 'Read this slice' }).click();
    await expect.poll(() => requests.chart.length).toBe(3);
    expect(requests.chart[2].searchParams.get('from')).toBe(
      '2026-10-17T12:00:00.000Z'
    );
    expect(requests.chart[2].searchParams.get('to')).toBe(
      '2026-10-17T18:00:00.000Z'
    );
    await expect(slices).toContainText('Slice 2 of 3');
    await expect(
      slices.getByLabel('Case span from (UTC)', { exact: true })
    ).toContainText('17 Oct 2026 06:00');
    await expect(
      slices.getByLabel('Case span to (UTC)', { exact: true })
    ).toContainText('17 Oct 2026 19:00');
    await expect(trendCard(page)).toContainText(
      'Chart origin: selected fixed source window'
    );
    await expect(
      trendCard(page).getByRole('combobox', { name: 'Range', exact: true })
    ).toHaveValue('Selected case slice');
    await trendCard(page).screenshot({
      path: info.outputPath(`health-manual-case-${scheme}.png`)
    });
    await page
      .getByRole('button', { name: 'Expand chart', exact: true })
      .click();
    const dialog = page.getByRole('dialog', { name: 'Expanded signal trend' });
    await expect(dialog.getByTestId('investigation-slices')).toContainText(
      'Slice 2 of 3'
    );
    await page.keyboard.press('Escape');
    await expect(slices).toContainText('Slice 2 of 3');
    expect(requests.chart).toHaveLength(3);
    await trendCard(page)
      .getByRole('combobox', { name: 'Range', exact: true })
      .click();
    await page.getByRole('option', { name: '5 minutes', exact: true }).click();
    await expect
      .poll(() => requests.chart.at(-1)?.searchParams.get('to'))
      .toBe(new Date(NOW).toISOString());
    await expect(trendCard(page)).toContainText(
      'Chart origin: recent source window'
    );
  });

test('late history cannot replace a newer binding and loading is explicit', async ({
  page
}) => {
  let release = () => {};
  let finishOldRoute = () => {};
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const oldRouteFinished = new Promise<void>((resolve) => {
    finishOldRoute = resolve;
  });
  let oldRequestSettled = false;
  const settled = (request: Request) => {
    const url = new URL(request.url());
    if (
      url.pathname.endsWith('/health/trend/') &&
      url.searchParams.get('binding') === '201'
    )
      oldRequestSettled = true;
  };
  page.on('requestfinished', settled);
  page.on('requestfailed', settled);
  const requests = await openHealth(page, {
    recordedTelemetryAsLive: false,
    signals: specs,
    entrySearch: search(),
    chartHandler: async (route, url) => {
      const binding = url.searchParams.get('binding');
      if (binding === '201') await pending;
      const spec = binding === '201' ? specs[0] : specs[1];
      const trend = trendFor(
        spec,
        Date.parse(url.searchParams.get('from')!),
        Date.parse(url.searchParams.get('to')!)
      );
      // Rendered identity differs even though both responses draw one curve.
      const samples = Array.from(
        { length: binding === '201' ? 3 : 7 },
        (_, index) => ({
          observed_at: new Date(Date.parse(from) + index * 5000).toISOString(),
          value: Number(binding) + index,
          quality: 'good'
        })
      );
      await route.fulfill({
        json: { ...trend, samples, source_name: `Binding ${binding} historian` }
      });
      if (binding === '201') finishOldRoute();
    }
  });
  const card = trendCard(page);
  await expect(card).toContainText('Reading source history');
  await expect
    .poll(() =>
      requests.chart.some((url) => url.searchParams.get('binding') === '201')
    )
    .toBe(true);
  await card.getByRole('combobox', { name: 'Parameter' }).click();
  await page
    .getByRole('option', { name: 'Bypass flow (m³/h)', exact: true })
    .click();
  await expect(card).toContainText('Source window returned');
  await expect(card.getByText('7 samples', { exact: true })).toBeVisible();
  await expect(card).toContainText('Binding 202 historian');
  const curve = card.locator('.recharts-area-curve');
  await expect(curve).toHaveCount(1);
  const newerPath = await curve.getAttribute('d');
  expect(newerPath).toBeTruthy();
  release();
  await oldRouteFinished;
  await expect.poll(() => oldRequestSettled).toBe(true);
  // Let any response-driven React update reach the painted chart before checking.
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
      )
  );
  await expect(card.getByRole('combobox', { name: 'Parameter' })).toHaveValue(
    'Bypass flow (m³/h)'
  );
  await expect(card.getByText('7 samples', { exact: true })).toBeVisible();
  await expect(card).toContainText('Binding 202 historian');
  await expect(card).not.toContainText('Binding 201 historian');
  await expect(curve).toHaveCount(1);
  await expect(curve).toHaveAttribute('d', newerPath!);
  expect(requests.chart.at(-1)?.searchParams.get('binding')).toBe('202');
});
