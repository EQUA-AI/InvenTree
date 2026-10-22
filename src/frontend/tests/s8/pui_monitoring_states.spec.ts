import { expect, test } from '@playwright/test';
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
import { contrast } from './contrast';
import { auditAxe, captureState, drawnBoundary } from './hyperagentEvidence';

nativeBaseline();
const designation = {
  critical: true,
  version: 2,
  reason: 'Synthetic engineer review',
  recorded_by: 'Synthetic engineer',
  recorded_at: '2026-10-18T08:00:00Z'
};
const activeSummary = {
  state: 'warning',
  active_anomaly_count: 1,
  anomaly_counts: { critical: 0, warning: 1, info: 0 }
};
const healthStates = [
  'stale',
  'no-data',
  'unknown',
  'critical-coverage-incomplete',
  'critical-coverage-complete',
  'signal-read-failed',
  'anomaly-read-failed',
  'threshold-breach',
  'shifted-plus',
  'shifted-minus',
  'past-open',
  'past-acknowledged',
  'past-resolved',
  'past-dismissed',
  'viewer',
  'editor'
] as const;
const perfStates = [
  '500-channels',
  'comparison-incompatible',
  'shared-views',
  'fresh-live',
  'search-narrowed',
  'viewer',
  'stale',
  'no-data',
  'unknown-evaluation',
  'threshold-breach',
  'limits-included',
  'custom-range',
  'history-read-failed',
  'partial-history'
] as const;
for (const scheme of ['light', 'dark'] as const) {
  for (const state of healthStates)
    test(`Health ${state} evidence ${scheme}`, async ({ page }, info) => {
      test.setTimeout(180_000);
      await page.setViewportSize(
        info.project.name.endsWith('phone')
          ? { width: 390, height: 844 }
          : { width: 1440, height: 900 }
      );
      let signals: HealthSignalSpec[] = [
        {
          ...HEALTH_SIGNALS[0],
          trend: 'steady',
          criticalDesignation: designation
        }
      ];
      let summary: Record<string, unknown> = {};
      let anomalies: ReturnType<typeof anomaly>[] = [];
      let pastAnomalies: ReturnType<typeof anomaly>[] = [];
      if (state === 'stale') {
        signals = signals.map((s) => ({
          ...s,
          stale: true,
          state: 'unknown',
          observedAt: new Date(NOW - 3600_000).toISOString()
        }));
        summary = { state: 'offline', last_observed_at: signals[0].observedAt };
      }
      if (state === 'no-data') {
        signals = signals.map((s) => ({
          ...s,
          value: null,
          observedAt: null,
          state: 'unknown',
          trend: 'empty'
        }));
        summary = { state: 'unknown', last_observed_at: null };
      }
      if (state === 'unknown') {
        signals = signals.map((s) => ({
          ...s,
          state: 'unknown',
          limits: undefined,
          criticalDesignation: undefined
        }));
        summary = { state: 'unknown' };
      }
      if (state === 'critical-coverage-incomplete')
        signals.push({
          ...signals[0],
          id: 299,
          key: '/fixture/critical/review',
          name: 'Critical flow mapping awaiting review',
          criticalDesignation: {
            ...designation,
            review_required: true,
            applies_current: false
          },
          state: 'normal'
        });
      if (state === 'threshold-breach') {
        signals = [
          { ...signals[0], value: 350, state: 'critical' },
          ...HEALTH_SIGNALS.slice(1)
        ];
        summary = { state: 'critical' };
      }
      if (
        ['viewer', 'editor', 'shifted-plus', 'shifted-minus'].includes(state)
      ) {
        const shift =
          state === 'shifted-plus'
            ? 10800
            : state === 'shifted-minus'
              ? -10800
              : 0;
        anomalies = [
          anomaly({
            recorded_telemetry_as_live: !shift,
            display_shifted: !!shift,
            display_shift_seconds: shift
          })
        ];
        summary = activeSummary;
      }
      if (state.startsWith('past-')) {
        const status =
          state === 'past-dismissed' ? 'suppressed' : state.slice(5);
        pastAnomalies = [
          anomaly({
            pk: 45,
            status: status as any,
            acknowledged_at:
              status === 'acknowledged'
                ? new Date(NOW - 120_000).toISOString()
                : null,
            title: `Synthetic ${state} alarm`,
            resolved_at: ['resolved', 'suppressed'].includes(status)
              ? new Date(NOW - 60_000).toISOString()
              : null
          })
        ];
        if (status === 'open' || status === 'acknowledged') {
          anomalies = [pastAnomalies[0]];
          summary = activeSummary;
        }
      }
      if (state === 'viewer' || state === 'editor')
        await page.addInitScript(
          (role) => {
            const url = new URL(location.href);
            url.searchParams.set('role', role);
            history.replaceState(null, '', url);
          },
          state === 'viewer' ? 'viewer' : 'editor'
        );
      await openHealth(page, {
        scheme,
        nativeTheme: true,
        compactChrome: true,
        signals,
        summary,
        anomalies,
        pastAnomalies,
        signalsStatus: state === 'signal-read-failed' ? 503 : undefined,
        anomaliesStatus: state === 'anomaly-read-failed' ? 503 : undefined
      });
      const root = page.getByTestId('pump-health');
      const heading = (name: string) =>
        page.getByRole('heading', { name, exact: true });
      let feature = () => heading('Current condition');
      if (state === 'stale') {
        await expect(signalTable(page)).toContainText('Last known');
        await expect(signalTable(page)).toContainText('60 min old');
        await expect(
          signalTable(page).getByText('Normal', { exact: true })
        ).toHaveCount(0);
        await expect(
          root.getByText('No current data', { exact: true })
        ).toBeVisible();
      }
      if (state === 'no-data')
        await expect(root).toContainText('No observation from 1 mapped signal');
      if (state === 'critical-coverage-incomplete') {
        feature = () =>
          root
            .getByRole('alert')
            .filter({ hasText: 'Critical coverage incomplete' });
        await expect(
          root
            .getByRole('alert')
            .filter({ hasText: 'Critical coverage incomplete' })
        ).toContainText('Critical flow mapping awaiting review');
        await expect(
          root
            .getByRole('alert')
            .filter({ hasText: 'Critical coverage incomplete' })
        ).toContainText('review required');
      }
      if (state === 'critical-coverage-complete') {
        await expect(root).toContainText(
          'Critical coverage: 1 of 1 designated signals evaluated now'
        );
        await expect(root.getByRole('alert')).toHaveCount(0);
      }
      if (state === 'signal-read-failed') {
        feature = () =>
          root
            .getByRole('alert')
            .filter({ hasText: 'Signal readings unavailable' });
        await expect(feature()).toBeVisible();
      }
      if (state === 'anomaly-read-failed') {
        feature = () =>
          root
            .getByRole('alert')
            .filter({ hasText: 'Active anomalies unavailable' });
        await expect(feature()).toBeVisible();
      }
      if (state === 'threshold-breach') {
        const row = signalTable(page).locator('tbody tr').first();
        await expect(row).toContainText('Discharge flow');
        await expect(root.getByLabel('Assessment cause')).toContainText(
          'Critical below 400 cusec'
        );
        const badge = root
          .getByTestId('health-summary')
          .locator('[data-tone="critical"]')
          .first();
        await expect(badge).toHaveAttribute('data-variant', 'filled');
        await expect(root.getByTestId('threshold-key')).toContainText(
          'Critical below 400 cusec'
        );
      }
      if (state.startsWith('shifted-')) {
        feature = () => heading('Active anomalies');
        await expect(
          root.getByText(
            state === 'shifted-plus' ? 'Shifted +3 h' : 'Shifted −3 h',
            { exact: true }
          )
        ).toHaveCount(2);
        await expect(root.getByText(/^Recorded:/)).toHaveCount(2);
      }
      if (state.startsWith('past-')) {
        if (state === 'past-open' || state === 'past-acknowledged') {
          await expect(root).not.toContainText('No active anomaly records');
          await expect(root.getByText(/^From active anomaly:/)).toContainText(
            `Synthetic ${state} alarm`
          );
        }
        const scroll = root.getByRole('region', {
          name: 'Past alarms; scroll for all columns',
          exact: true
        });
        await expect(scroll).toHaveAttribute('tabindex', '0');
        if (page.viewportSize()!.width === 390) {
          await scroll.focus();
          await page.keyboard.press('ArrowRight');
          await expect
            .poll(() => scroll.evaluate((el) => el.scrollLeft))
            .toBeGreaterThan(0);
        }
        if (state === 'past-acknowledged') {
          await expect(root).toContainText('recorder unavailable');
          await expect(root).not.toContainText('Nobody, before it ended');
        }
        feature = () =>
          root
            .getByTestId('alarm-history')
            .getByText(
              state === 'past-open'
                ? 'Active · not acknowledged'
                : state === 'past-acknowledged'
                  ? 'Active · acknowledged'
                  : state === 'past-resolved'
                    ? 'Resolved'
                    : 'Dismissed',
              { exact: true }
            );
        await expect(root).toContainText(`Synthetic ${state} alarm`);
        // Active anomalies and past-alarm records each retain their status.
        // This branch proves the status of the returned history record.
        await expect(feature()).toBeVisible();
      }
      if (state === 'viewer' || state === 'editor') {
        feature = () => heading('Active anomalies');
        const actions = root.getByRole('button', {
          name: /^(Analyze|Acknowledge|Dismiss|Create repair)\b/
        });
        await expect(actions).toHaveCount(state === 'viewer' ? 0 : 4);
        if (state === 'viewer')
          await expect(root.getByText(/^View only:/)).toBeVisible();
        else
          for (const action of await actions.all())
            await expect(action).toBeEnabled();
      }
      await captureState(page, 'health', `health-${state}`, scheme, feature);
      if (state === 'threshold-breach') {
        await captureState(
          page,
          'health',
          'health-threshold-breach-row',
          scheme,
          () => signalTable(page).locator('tbody tr').first()
        );
        await captureState(
          page,
          'health',
          'health-threshold-breach-trend',
          scheme,
          () => root.getByTestId('health-trend-plot')
        );
      }
      if (state === 'viewer' || state.startsWith('past-'))
        await auditAxe(
          page,
          'health',
          `health-${state}-${scheme}-${info.project.name}`
        );
    });
  for (const state of perfStates)
    test(`Performance ${state} evidence ${scheme}`, async ({ page }, info) => {
      test.setTimeout(180_000);
      await page.setViewportSize(
        info.project.name.endsWith('phone')
          ? { width: 390, height: 844 }
          : { width: 1440, height: 900 }
      );
      await page.clock.setFixedTime(new Date(NOW));
      let signals = [
        signal(
          '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1',
          130,
          'degC',
          'Winding 1',
          { limits: { warn_max: 125, critical_max: 145 }, state: 'warning' }
        )
      ];
      if (state === 'unknown-evaluation')
        signals = signals.map((s) => ({ ...s, state: 'unknown' }));
      if (state === 'stale')
        signals = signals.map((s) => ({
          ...s,
          stale: true,
          state: 'unknown',
          observedAt: new Date(NOW - 3600_000).toISOString()
        }));
      if (state === 'no-data')
        signals = signals.map((s) => ({
          ...s,
          value: null,
          state: 'unknown',
          observedAt: null
        }));
      if (['comparison-incompatible', 'shared-views'].includes(state))
        signals = [
          signal('/dex/PUMP1_SPEED', 745, 'rpm', 'Motor shaft speed'),
          ...signals
        ];
      if (
        [
          'fresh-live',
          'viewer',
          'custom-range',
          'search-narrowed',
          'history-read-failed',
          'partial-history'
        ].includes(state)
      )
        signals = [...NATIVE_SIGNALS];
      if (state === 'partial-history')
        signals = [
          ...NATIVE_SIGNALS,
          ...Array.from({ length: 210 }, (_, i) =>
            signal(`/dex/PUMP1_PARTIAL_${i}`, i, 'bar', `Partial fixture ${i}`)
          )
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
                  name: 'Synthetic engineer investigation',
                  config: { search: '', section: 'all', bindings: [100, 101] },
                  owner_id: 99,
                  owner: 'Synthetic creator',
                  is_default: true,
                  version: 3,
                  recorded_at: '2026-10-18T08:00:00Z',
                  updated_by: 'Synthetic engineer',
                  updated_at: '2026-10-18T09:00:00Z'
                }
              ]
            }
          })
      );
      const compare = ['comparison-incompatible', 'shared-views'].includes(
        state
      );
      await (state === 'fresh-live' ? openPerformance : openPausedPerformance)(
        page,
        {
          settleInitialRead: true,
          scheme,
          harness: 's3',
          nativeTheme: true,
          compactChrome: true,
          signals,
          harnessParams: {
            role: state === 'viewer' ? 'viewer' : 'engineer',
            ...(compare
              ? { entry: '/machines/machine/18/performance?compare=100,101' }
              : {})
          },
          failSeries:
            state === 'history-read-failed'
              ? () => true
              : state === 'partial-history'
                ? (url) => url.searchParams.has('keys')
                : undefined
        }
      );
      const root = page.getByTestId('pump-performance');
      let feature = () => root.locator('[data-kpi="winding"]');
      if (state === 'fresh-live') {
        feature = () => root.getByText('Live', { exact: true }).last();
        const badge = root.locator('[data-tone="live"]');
        await expect(badge).toHaveAttribute('data-variant', 'outline');
        expect((await contrast(badge)).ratio).toBeGreaterThanOrEqual(4.5);
      }
      if (state === 'viewer') {
        await expect(
          page.getByRole('button', { name: /^(Create repair|Start repair)\b/ })
        ).toHaveCount(0);
        await root
          .getByRole('button', {
            name: 'Comparisons and saved views',
            exact: true
          })
          .click();
        await expect(
          root.getByRole('button', { name: 'Create shared view', exact: true })
        ).toHaveCount(0);
      }
      if (state === 'stale') {
        const tile = root.locator('[data-kpi="winding"]');
        await expect(tile).toContainText('Last known');
        await expect(tile).toContainText('60 min old');
        await expect(tile).toContainText('freshness budget 300 s');
        await expect(tile.locator('[data-kpi-value]')).toHaveCSS(
          'font-weight',
          '400'
        );
        await expect(
          tile.getByRole('img', {
            name: /^(Rising|Falling|Steady) over the window$/
          })
        ).toHaveCount(0);
        await expect(
          tile.getByRole('button', {
            name: /^(Rising|Falling|Steady) over the window$/
          })
        ).toHaveCount(0);
        await expect(
          tile.locator(
            '[data-tone="normal"],[data-tone="warning"],[data-tone="critical"]'
          )
        ).toHaveCount(0);
      }
      if (state === 'unknown-evaluation') {
        feature = () => root.getByText('Limits not assessed', { exact: true });
        await expect(
          root.getByText('Limits not assessed', { exact: true })
        ).toBeVisible();
        await expect(root.getByText(/within limits/)).toHaveCount(0);
      }
      if (state === 'no-data')
        await expect(root.locator('[data-kpi="winding"]')).toContainText(
          'Unavailable'
        );
      if (state === 'comparison-incompatible') {
        feature = () => root.getByTestId('comparison-refusal');
        await expect(feature()).toContainText('Winding 1 (°C, winding)');
        await expect(feature()).toContainText('Shaft speed (rpm, speed)');
      }
      if (state === 'shared-views') {
        await root
          .getByRole('button', { name: 'Read shared views', exact: true })
          .click();
        feature = () => root.getByText('Shared display views', { exact: true });
        await expect(root.getByTestId('shared-display-views')).toContainText(
          'Synthetic engineer'
        );
      }
      if (state === '500-channels' || state === 'search-narrowed') {
        const nav = root.getByRole('button', { name: /^Channels ·/ });
        if (await nav.isVisible()) await nav.click();
        if (state === '500-channels') {
          const electrical = root.locator('[data-section="Electrical"]');
          await expect(electrical.locator('.recharts-surface')).toHaveCount(0);
          await root
            .getByRole('combobox', { name: 'Section', exact: true })
            .click();
          await page
            .getByRole('option', { name: 'Full overview', exact: true })
            .click();
          await expect(
            electrical.locator('.recharts-surface').first()
          ).toBeVisible();
          await root
            .getByRole('combobox', { name: 'Section', exact: true })
            .click();
          await page.getByRole('option', { name: /^Other signals/ }).click();
          const table = root.getByRole('table', {
            name: 'Other channels',
            exact: true
          });
          await expect(table.locator('tbody tr')).toHaveCount(50);
          const scroll = root.getByRole('region', {
            name: 'Other channels; scroll for all columns',
            exact: true
          });
          await expect(scroll).toHaveAttribute('tabindex', '0');
          if (page.viewportSize()!.width === 390) {
            await scroll.focus();
            await page.keyboard.press('ArrowRight');
            await expect
              .poll(() => scroll.evaluate((el) => el.scrollLeft))
              .toBeGreaterThan(0);
          }
          feature = () => root.getByText(/^Showing 1–50 of/);
        } else {
          await root
            .getByRole('textbox', { name: 'Find a channel', exact: true })
            .fill('SPARE_INSTRUMENT_7');
          await expect(
            root.getByRole('combobox', { name: 'Section', exact: true })
          ).toHaveValue('Search results · 1');
          feature = () => root.getByTestId('channel-navigator');
        }
      }
      if (state === 'threshold-breach' || state === 'limits-included') {
        const section = root.locator(
          '[data-section="Motor winding temperature"]'
        );
        feature = () => section.getByTestId('threshold-key');
        await expect(section.locator('[data-tone="warning"]')).toContainText(
          '1 of 1 above warning (Warning above 125 °C)'
        );
        await expect(
          root.locator('[data-kpi="winding"] [data-tone="warning"]')
        ).toBeVisible();
        if (state === 'limits-included') {
          const include = section.getByRole('switch', {
            name: 'Include limits in scale'
          });
          await expect(include).toBeVisible();
          // Click the visible native track: the transparent input spans the
          // whole Stack row, whose centre can be empty space.
          const track = section.locator('.mantine-Switch-track');
          await expect(track).toHaveCount(1);
          await track.evaluate((el) => el.scrollIntoView({ block: 'center' }));
          await track.click();
          await expect(include).toBeChecked();
          await drawnBoundary(
            section.locator('.recharts-reference-line line').first()
          );
        }
      }
      if (state === 'custom-range') {
        await root.getByText('Custom', { exact: true }).click();
        for (const [label, day, confirm] of [
          ['From', '17 October 2026', 'Confirm start'],
          ['To', '18 October 2026', 'Confirm end']
        ]) {
          await root.getByRole('button', { name: label, exact: true }).click();
          const dropdown = page.locator('[data-dates-dropdown]').last();
          await dropdown
            .getByRole('button', { name: day, exact: true })
            .click();
          await dropdown
            .getByRole('button', { name: confirm, exact: true })
            .click();
          await expect(
            root.getByRole('button', { name: label, exact: true })
          ).not.toContainText(
            label === 'From' ? 'Start of window' : 'End of window'
          );
        }
        feature = () => root.getByText('History held from', { exact: false });
      }
      if (state === 'history-read-failed') {
        feature = () =>
          root
            .getByRole('alert')
            .filter({ hasText: 'History read failed' })
            .first();
        await expect(feature()).toBeVisible();
      }
      if (state === 'partial-history') {
        feature = () =>
          root.getByRole('alert').filter({ hasText: 'Partial history read' });
        await expect(feature()).toBeVisible();
      }
      await captureState(
        page,
        'performance',
        `performance-${state}`,
        scheme,
        feature
      );
      if (state === 'threshold-breach') {
        await captureState(
          page,
          'performance',
          'performance-threshold-breach-chip',
          scheme,
          () =>
            root.locator(
              '[data-section="Motor winding temperature"] [data-tone="warning"]'
            )
        );
        await captureState(
          page,
          'performance',
          'performance-threshold-breach-kpi',
          scheme,
          () => root.locator('[data-kpi="winding"]')
        );
      }
      if (state === 'viewer')
        await auditAxe(
          page,
          'performance',
          `performance-${scheme}-${info.project.name}`
        );
    });
}
