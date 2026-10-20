import { expect, test } from '@playwright/test';
import { NOW as PERFORMANCE_NOW } from '../performance/fixtures';
import { nativeBaseline } from '../s0/guards';
import {
  HEALTH_SIGNALS,
  type HealthSignalSpec,
  NOW,
  anomaly,
  openHealth,
  signalTable
} from '../s3/healthFixtures';
import {
  NATIVE_SIGNALS,
  openPausedPerformance,
  openPerformance,
  signal
} from '../s3/performanceFixtures';
import { captureMatrix } from './monitoringEvidence';

nativeBaseline();
const designation = {
  critical: true,
  version: 2,
  reason: 'Fixture engineer review',
  recorded_by: 'Synthetic engineer',
  recorded_at: '2026-10-18T08:00:00Z'
};
for (const scheme of ['light', 'dark'] as const) {
  for (const state of [
    'stale',
    'no-data',
    'unknown',
    'critical-coverage-missing',
    'threshold-breach',
    'shifted-alarms'
  ] as const) {
    test(`Health ${state} evidence ${scheme}`, async ({ page }) => {
      test.setTimeout(180_000);
      let signals: HealthSignalSpec[] = [
        {
          ...HEALTH_SIGNALS[0],
          trend: 'steady',
          criticalDesignation: designation
        }
      ];
      let summary: Record<string, unknown> = {};
      if (state === 'stale') {
        signals = signals.map((s) => ({
          ...s,
          stale: true,
          state: 'offline',
          observedAt: new Date(NOW - 3600_000).toISOString()
        }));
        summary = { state: 'offline', last_observed_at: signals[0].observedAt };
      } else if (state === 'no-data') {
        signals = signals.map((s) => ({
          ...s,
          value: null,
          observedAt: null,
          state: 'unknown',
          trend: 'empty'
        }));
        summary = { state: 'unknown', last_observed_at: null };
      } else if (state === 'unknown') {
        signals = signals.map((s) => ({
          ...s,
          state: 'unknown',
          limits: undefined,
          criticalDesignation: undefined
        }));
        summary = { state: 'unknown' };
      } else if (state === 'critical-coverage-missing') {
        signals.push({
          ...signals[0],
          id: 299,
          key: '/fixture/critical/missing',
          name: 'Missing critical measurement',
          value: null,
          observedAt: null,
          state: 'unknown',
          trend: 'empty'
        });
      } else if (state === 'shifted-alarms') {
        summary = {
          state: 'warning',
          active_anomaly_count: 1,
          anomaly_counts: { critical: 0, warning: 1, info: 0 }
        };
      } else if (state === 'threshold-breach') {
        signals = signals.map((s) => ({ ...s, value: 350, state: 'critical' }));
        summary = { state: 'critical' };
      }
      await openHealth(page, {
        scheme,
        nativeTheme: true,
        compactChrome: true,
        signals,
        summary,
        ...(state === 'shifted-alarms'
          ? {
              anomalies: [
                anomaly({ display_shifted: true, display_shift_seconds: 93600 })
              ],
              pastAnomalies: [
                anomaly({
                  pk: 45,
                  status: 'resolved',
                  display_shifted: true,
                  display_shift_seconds: 93600
                })
              ]
            }
          : {})
      });
      if (state === 'shifted-alarms') {
        await expect(
          page.getByText('Shown 1 d 2 h later than recorded', { exact: true })
        ).toHaveCount(2);
        await expect(
          page.getByTestId('pump-health').getByText(/Times: your clock/)
        ).toContainText('shifted anomaly times are labelled separately');
      }
      if (state === 'stale')
        await expect(signalTable(page)).toContainText(
          'Last known · not current'
        );
      if (state === 'no-data')
        await expect(signalTable(page)).toContainText(
          'Unavailable · no observation'
        );
      if (state === 'unknown')
        await expect(
          page.getByText('Critical coverage unavailable', { exact: true })
        ).toBeVisible();
      if (state === 'critical-coverage-missing')
        await expect(
          page.getByText('2 designated · 1 usable now · 1 evaluated now', {
            exact: true
          })
        ).toBeVisible();
      if (state === 'threshold-breach') {
        await signalTable(page)
          .getByRole('button', {
            name: 'Details for Discharge flow',
            exact: true
          })
          .click();
        await expect(signalTable(page)).toContainText(
          'Critical below 400 m3/h'
        );
        await expect(signalTable(page)).toContainText('Fixture historian');
      }
      await captureMatrix(page, 'health', state, scheme);
    });
  }
  for (const state of [
    'stale',
    'no-data',
    'threshold-breach',
    'comparison-incompatible',
    'shared-views',
    '500-channels',
    'fresh-live'
  ] as const) {
    test(`Performance ${state} evidence ${scheme}`, async ({ page }) => {
      test.setTimeout(180_000);
      await page.clock.setFixedTime(new Date(PERFORMANCE_NOW));
      let signals = [
        signal(
          '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1',
          130,
          'degC',
          'Winding 1',
          { limits: { warn_max: 125, critical_max: 145 }, state: 'warning' }
        )
      ];
      if (state === 'stale')
        signals = signals.map((s) => ({ ...s, stale: true, state: 'unknown' }));
      if (state === 'no-data')
        signals = signals.map((s) => ({ ...s, value: null, state: 'unknown' }));
      if (state === 'comparison-incompatible' || state === 'shared-views')
        signals = [
          signal('/dex/PUMP1_SPEED', 745, 'rpm', 'Motor shaft speed'),
          ...signals
        ];
      if (state === '500-channels')
        signals = [
          ...NATIVE_SIGNALS.slice(0, 40),
          ...Array.from({ length: 460 }, (_, i) =>
            signal(`/dex/PUMP1_EXTRA_${i}`, i, 'bar', `Extra instrument ${i}`)
          )
        ];
      await page.route(
        '**/api/assets/recorded/18/performance-views/',
        (route) =>
          route.fulfill({
            json: {
              results: [
                {
                  id: 9,
                  name: 'Engineer investigation set',
                  config: { search: '', section: 'all', bindings: [100, 101] },
                  owner_id: 99,
                  owner: 'Synthetic creator',
                  is_default: true,
                  version: 3,
                  recorded_at: '2026-09-26T08:00:00Z',
                  updated_by: 'Synthetic engineer',
                  updated_at: '2026-09-26T09:00:00Z'
                }
              ]
            }
          })
      );
      const open =
        state === 'fresh-live' ? openPerformance : openPausedPerformance;
      const requests = await open(page, {
        settleInitialRead: true,
        scheme,
        harness: 's3',
        nativeTheme: true,
        compactChrome: true,
        signals
      });
      if (state === 'fresh-live') {
        await expect(
          page.getByRole('switch', { name: 'Live', exact: true })
        ).toBeChecked();
        const live = page
          .locator('.mantine-Badge-root')
          .filter({ hasText: /^Live$/ });
        await expect(live).toHaveCount(1);
        await expect(live).toBeVisible();
      }
      if (state === 'no-data')
        await expect(page.locator('[data-kpi="winding"]')).toContainText(
          'Unavailable · no usable observation'
        );
      if (state === 'threshold-breach')
        await expect(page.getByTestId('threshold-key')).toContainText(
          'Warning above 125'
        );
      if (state === 'comparison-incompatible' || state === 'shared-views') {
        await page
          .getByRole('button', {
            name: 'Comparisons and display views',
            exact: true
          })
          .click();
        for (const name of ['Shaft speed', 'Winding 1']) {
          await page
            .getByRole('combobox', { name: 'Comparison channels', exact: true })
            .fill(name);
          await page
            .getByRole('option')
            .filter({ hasText: name })
            .first()
            .click();
          await page.keyboard.press('Escape');
        }
        await expect(page.locator('[data-comparison-group]')).toHaveCount(2);
        await expect(
          page.getByText(
            'Different engineering roles or units: selected channels use separate synchronized charts.',
            { exact: true }
          )
        ).toBeVisible();
      }
      if (state === 'shared-views') {
        await page
          .getByRole('button', { name: 'Read shared views', exact: true })
          .click();
        await expect(page.getByTestId('shared-display-views')).toContainText(
          'Synthetic engineer'
        );
        await page
          .getByRole('button', { name: 'Restore shared view', exact: true })
          .click();
        await expect(page.locator('[data-comparison-group]')).toHaveCount(2);
      }
      if (state === '500-channels') {
        await page
          .getByLabel('Find a channel', { exact: true })
          .fill('EXTRA_459');
        await expect(
          page.getByRole('button', { name: /Extra instrument 459 ·/ })
        ).toBeVisible();
        expect(requests).toHaveLength(3);
      }
      await captureMatrix(page, 'performance', state, scheme);
    });
  }
}
