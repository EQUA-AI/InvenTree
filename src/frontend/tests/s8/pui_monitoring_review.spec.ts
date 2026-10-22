import { expect, test } from '@playwright/test';
import { NOW as PERFORMANCE_NOW } from '../performance/fixtures';
import { nativeBaseline } from '../s0/guards';
import { anomaly, openHealth } from '../s3/healthFixtures';
import { openPausedPerformance } from '../s3/performanceFixtures';
import { readable } from './contrast';
import { captureMatrix, monitoringMeasurements } from './monitoringEvidence';

nativeBaseline();
for (const scheme of ['light', 'dark'] as const) {
  test(`monitoring hides viewer actions and keeps coverage readable and inspectable ${scheme}`, async ({
    page
  }) => {
    await page.addInitScript(() => {
      const url = new URL(location.href);
      url.searchParams.set('role', 'viewer');
      history.replaceState(null, '', url);
    });
    await openHealth(page, {
      scheme,
      nativeTheme: true,
      compactChrome: true,
      anomalies: [anomaly()]
    });
    const actions = page.getByTestId('pump-health').getByRole('button', {
      name: /^(Analyze|Acknowledge|Dismiss|Create repair)\b/
    });
    await expect(actions).toHaveCount(0);
    await expect(page.getByText(/^View only:/)).toBeVisible();
    const coverage = page.getByRole('button', {
      name: /critical coverage unavailable$/
    });
    await coverage.focus();
    await expect(page.getByRole('tooltip')).toContainText(
      'No critical-signal designation is supplied.'
    );
    await readable(coverage);
    await readable(page.getByRole('tooltip'));
    const measurements = await monitoringMeasurements(page, 'health');
    const coverageTitle = measurements.text.find((row) =>
      row.text.endsWith('critical coverage unavailable')
    );
    expect(coverageTitle?.ratio).toBeGreaterThanOrEqual(4.5);
    expect(
      await coverage.evaluate((node) => getComputedStyle(node).color)
    ).toBe(
      await coverage.evaluate(
        (node) => getComputedStyle(node.parentElement!).color
      )
    );
    await captureMatrix(page, 'health', 'viewer-actions', scheme);
  });

  test(`Performance extent uses the full shared instant format ${scheme}`, async ({
    page
  }) => {
    await page.clock.setFixedTime(new Date(PERFORMANCE_NOW));
    await openPausedPerformance(page, {
      scheme,
      nativeTheme: true,
      compactChrome: true,
      harness: 's3'
    });
    await page.getByText('Custom', { exact: true }).click();
    await expect(
      page.getByText(
        'History held from 8 Oct 2026 12:00:00 to 18 Oct 2026 12:00:00.',
        { exact: true }
      )
    ).toBeVisible();
    await captureMatrix(page, 'performance', 'custom-range', scheme);
  });
}
