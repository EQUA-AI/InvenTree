import { expect, test } from '@playwright/test';
import { NOW as PERFORMANCE_NOW } from '../performance/fixtures';
import { nativeBaseline } from '../s0/guards';
import { anomaly, openHealth } from '../s3/healthFixtures';
import { openPausedPerformance } from '../s3/performanceFixtures';
import { captureMatrix, monitoringMeasurements } from './monitoringEvidence';

nativeBaseline();
for (const scheme of ['light', 'dark'] as const) {
  test(`monitoring hides viewer actions and keeps the attention title local ${scheme}`, async ({
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
    const title = await page
      .getByRole('alert')
      .filter({ hasText: 'Critical coverage unavailable' })
      .evaluate((alert) => {
        const clone = alert.cloneNode(true) as HTMLElement;
        document.body.appendChild(clone);
        const result = {
          panel: getComputedStyle(alert.querySelector('.mantine-Alert-label')!)
            .color,
          native: getComputedStyle(clone.querySelector('.mantine-Alert-label')!)
            .color
        };
        clone.remove();
        return result;
      });
    expect(title.panel).toBe(title.native);
    const measurements = await monitoringMeasurements(page, 'health');
    const coverageTitle = measurements.text.find(
      (row) => row.text === 'Critical coverage unavailable'
    );
    expect(coverageTitle?.ratio).toBeGreaterThanOrEqual(4.5);
    // Also catches treating normalized color(srgb) components as 0..255 RGB.
    expect(coverageTitle?.ratio).toBeCloseTo(
      scheme === 'light' ? 7.018346 : 13.091108,
      2
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
