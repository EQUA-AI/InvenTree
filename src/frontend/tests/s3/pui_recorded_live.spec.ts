import { expect, test } from '@playwright/test';
import type { FaultOccurrence } from '../../src/pages/assets/faults/occurrenceModel';
import { nativeBaseline } from '../s0/guards';
import { NOW, openHealth, trendCard } from './healthFixtures';
import {
  mockFaultHistory,
  occurrenceDetail,
  openFaultHistory
} from './machineWorkFixtures';
import { openPerformance } from './performanceFixtures';
import {
  historyInformation,
  samplingInformation
} from './presentationAssertions';

nativeBaseline();
for (const asLive of [true, false]) {
  test(`O27 fault occurrence ${asLive ? 'on' : 'off'} retains canonical plant links and captured evidence`, async ({
    page
  }, info) => {
    const original = occurrenceDetail(901);
    const delta = 86400_000;
    const row = {
      ...original,
      recorded_telemetry_as_live: asLive,
      display_shifted: true,
      display_shift_seconds: delta / 1000,
      first_observed_at: asLive
        ? new Date(Date.parse(original.first_observed_at) + delta).toISOString()
        : original.first_observed_at,
      last_observed_at: asLive
        ? new Date(Date.parse(original.last_observed_at) + delta).toISOString()
        : original.last_observed_at
    } as FaultOccurrence;
    await mockFaultHistory(page, { rows: [row] });
    await openFaultHistory(page, { query: '?occurrence=901' });
    const evidence = page.getByTestId('occurrence-evidence');
    await expect(evidence).toBeVisible();
    await expect(
      evidence.getByRole('button', { name: /^Case span from/ })
    ).toHaveText(asLive ? /^13 Oct 2026 / : /^12 Oct 2026 /);
    await expect(
      evidence.getByRole('button', { name: /^Case span to/ })
    ).toHaveText(asLive ? /^13 Oct 2026 / : /^12 Oct 2026 /);
    const link = evidence.getByRole('link', {
      name: 'Trend around first observation · 30 min before and after'
    });
    const url = new URL(
      (await link.getAttribute('href'))!,
      'https://example.invalid'
    );
    expect(url.searchParams.get('history_clock')).toBe('recorded');
    expect(url.searchParams.get('history_from')).toBe(
      new Date(
        Date.parse(original.first_observed_at) - 30 * 60000
      ).toISOString()
    );
    expect(url.searchParams.get('history_to')).toBe(
      new Date(
        Date.parse(original.first_observed_at) + 30 * 60000
      ).toISOString()
    );
    await expect(evidence.locator('[data-snapshot]')).not.toContainText(
      'Outside occurrence span'
    );
    await expect(evidence.locator('[data-snapshot]')).toContainText(
      '12 Oct 2026'
    );
    if (asLive) await expect(evidence).not.toContainText('later than recorded');
    else await expect(evidence).toContainText('later than recorded');
    await evidence.screenshot({
      path: info.outputPath(`o27-fault-${asLive ? 'on' : 'off'}.png`),
      animations: 'disabled'
    });
  });
  test(`O27 Health presentation ${asLive ? 'on' : 'off'} retains source failures and the source clock`, async ({
    page
  }, info) => {
    const series: URL[] = [];
    await page.route(
      '**/api/machine-health/machines/18/health/series/**',
      async (route) => {
        const url = new URL(route.request().url());
        series.push(url);
        const from = url.searchParams.get('from')!;
        const to = url.searchParams.get('to')!;
        await route.fulfill({
          json: {
            machine: 18,
            station: null,
            window_start: from,
            window_end: to,
            window_seconds: (Date.parse(to) - Date.parse(from)) / 1000,
            mode: 'sampled',
            slots: 240,
            resolution_seconds: 10800,
            cadence_seconds: null,
            expected_documents: 518400,
            documents_read: 240,
            recorded_telemetry_as_live: true,
            display_shifted: true,
            display_shift_seconds: 400 * 86400,
            live: {
              enabled: true,
              last_poll_at: '2025-08-01T00:00:00Z',
              last_error_code: '',
              poll_interval_seconds: 60
            },
            limits: { max_window_seconds: 2592000, max_points: 4320 },
            series: [
              {
                binding_id: Number(url.searchParams.get('bindings')),
                available: true,
                samples: [
                  { t: Date.parse(from), v: 1, q: 'good' },
                  { t: Date.parse(to), v: 2, q: 'good' }
                ]
              }
            ]
          }
        });
      }
    );
    await openHealth(page, {
      recordedTelemetryAsLive: asLive,
      dataRange: {
        from: new Date(NOW - 30 * 86400_000).toISOString(),
        display_shift_seconds: 400 * 86400
      },
      summary: {
        state: 'warning',
        active_anomaly_count: 1,
        anomaly_counts: { critical: 0, warning: 1, info: 0 },
        last_observed_at: new Date(NOW).toISOString(),
        sources: [
          {
            source_id: 1,
            name: 'Synthetic source',
            source_type: 'scada',
            active: true,
            healthy: true,
            reporting: true,
            recorded_telemetry_as_live: asLive,
            last_observed_at: new Date(NOW).toISOString(),
            last_success_at: '2025-08-01T00:00:00Z',
            last_error_at: null,
            last_error_code: '',
            freshness_threshold_seconds: 300,
            mapped_tag_count: 4
          }
        ]
      },
      chartOverrides: {
        recorded_telemetry_as_live: asLive,
        display_shifted: true,
        display_shift_seconds: 400 * 86400
      },
      anomalies: [
        {
          recorded_telemetry_as_live: asLive,
          display_shifted: true,
          display_shift_seconds: 10800
        }
      ]
    });
    const card = trendCard(page);
    await expect(card.getByText(/^Selected window:/)).toHaveCount(0);
    await historyInformation(page, card, 'Fixture historian');
    if (asLive) {
      await expect(page.getByTestId('health-summary')).toContainText(
        'just now'
      );
      await expect(
        page.getByText('Reporting', { exact: true }).first()
      ).toBeVisible();
      await expect(page.getByTestId('anomaly-time-shift')).toHaveCount(0);
      await expect(card).not.toContainText(
        'Times are shown on the dashboard clock'
      );
      await card.getByRole('combobox', { name: 'Range', exact: true }).click();
      await page.getByRole('option', { name: '30 days', exact: true }).click();
      await expect.poll(() => series.length).toBe(1);
      expect(
        Date.parse(series[0].searchParams.get('to')!) -
          Date.parse(series[0].searchParams.get('from')!)
      ).toBe(30 * 86400_000);
      expect(series[0].searchParams.get('points')).toBe('240');
      await historyInformation(
        page,
        card,
        'Sampled history; gaps are not filled'
      );
    } else {
      await expect(
        page.getByTestId('anomaly-time-shift').first()
      ).toContainText('Shifted +3 h');
      await expect(
        card.getByText('Times are shown on the dashboard clock')
      ).toHaveCount(0);
      await expect(
        page.getByTestId('anomaly-time-shift').first()
      ).toContainText('Shifted +3 h');
      await card.getByRole('combobox', { name: 'Range', exact: true }).click();
      await expect(
        page.getByRole('option', { name: '30 days', exact: true })
      ).toHaveCount(0);
      await page.keyboard.press('Escape');
    }
    await card.screenshot({
      path: info.outputPath(`o27-health-${asLive ? 'on' : 'off'}.png`),
      animations: 'disabled'
    });
    await page.route('**/api/machine-health/machines/18/health/', (route) =>
      route.fulfill({
        status: 503,
        json: { detail: 'Synthetic source read failed' }
      })
    );
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: 'Health data unavailable' })
    ).toBeVisible();
  });

  test(`O27 Performance presentation ${asLive ? 'on' : 'off'} offers the permitted range`, async ({
    page
  }, info) => {
    let failure: 'none' | 'source' | 'unavailable' | 'empty' = 'none';
    const requests = await openPerformance(page, {
      harness: 's3',
      settleInitialRead: true,
      recordedTelemetryAsLive: asLive,
      displayShift: 400 * 86400,
      mapSeriesResponse: (response) => ({
        ...response,
        live: {
          ...response.live,
          reporting: failure === 'none' || failure === 'empty',
          last_observed_at: new Date(NOW).toISOString(),
          last_error_code: failure === 'source' ? 'SOURCE_TIMEOUT' : ''
        },
        series:
          failure === 'unavailable' || failure === 'empty'
            ? response.series.map((entry) => ({
                ...entry,
                available: failure === 'empty',
                reason: failure === 'empty' ? undefined : 'SOURCE_UNAVAILABLE',
                count: 0,
                samples: []
              }))
            : response.series
      })
    });
    const status = page.getByTestId('live-indicator');
    const disclosure =
      'Recorded source presented on the live clock; timestamps shifted by 400 d.';
    const live = status.getByRole('switch', {
      name: `Live updates: Live · ${disclosure}`,
      exact: true
    });
    await expect(status).toBeVisible();
    if (asLive) {
      await expect(status).not.toContainText('Source history display shift');
      const selectedRangeRead = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          url.pathname === '/api/machine-health/machines/18/health/series/' &&
          Date.parse(url.searchParams.get('to')!) -
            Date.parse(url.searchParams.get('from')!) ===
            30 * 86400_000
        );
      });
      await page.getByText('30 d', { exact: true }).click();
      await expect
        .poll(() =>
          requests.some(
            (url) =>
              Date.parse(url.searchParams.get('to')!) -
                Date.parse(url.searchParams.get('from')!) ===
              30 * 86400_000
          )
        )
        .toBe(true);
      await (await selectedRangeRead).finished();
      await expect(status).toContainText('Sampled: one reading every 3 h', {
        timeout: 60000
      });
      await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
      await samplingInformation(page, 'Sampled: one reading every 3 h');
      await expect(live).toBeChecked();
      await expect(status.locator('time')).toHaveCount(1);
      await expect(status.getByText('Live', { exact: true })).toHaveCount(1);
    } else {
      await expect(status).not.toContainText('Source history display shift');
      await historyInformation(page);
      await expect(page.getByText('30 d', { exact: true })).toHaveCount(0);
    }
    await page.keyboard.press('Tab');
    await live.focus();
    await expect(
      page.getByRole('tooltip', {
        name: /Recorded source presented on the live clock/
      })
    ).toContainText(disclosure);
    await live.blur();
    await status.screenshot({
      path: info.outputPath(`o27-performance-${asLive ? 'on' : 'off'}.png`),
      animations: 'disabled'
    });
    if (asLive) {
      failure = 'source';
      await page.getByRole('button', { name: 'Refresh', exact: true }).click();
      await expect(
        status.getByText('Read failed', { exact: true })
      ).toBeVisible();
      failure = 'unavailable';
      await page.getByRole('button', { name: 'Refresh', exact: true }).click();
      await expect(status).toContainText('Readings unavailable');
      await expect(status.locator('[data-tone="live"]')).toHaveCount(0);
      failure = 'empty';
      await page.getByRole('button', { name: 'Refresh', exact: true }).click();
      await expect(
        status.getByText('No readings', { exact: true })
      ).toBeVisible();
      await expect(status.locator('[data-tone="live"]')).toHaveCount(0);
    }
  });
}
