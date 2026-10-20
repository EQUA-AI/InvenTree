import { expect, test } from '@playwright/test';
import { NOW as PERFORMANCE_NOW } from '../performance/fixtures';
import { nativeBaseline } from '../s0/guards';
import { anomaly, openHealth } from '../s3/healthFixtures';
import { openPausedPerformance } from '../s3/performanceFixtures';
import { captureMatrix, monitoringMeasurements } from './monitoringEvidence';

nativeBaseline();
for (const scheme of ['light', 'dark'] as const) {
  test(`monitoring keeps native disabled gray actions and alert title styles ${scheme}`, async ({
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
    await expect(actions).toHaveCount(4);
    for (const button of await actions.all())
      await expect(button).toBeDisabled();
    const comparison = await page
      .getByRole('button', { name: /^Dismiss\b/ })
      .evaluate((button) => {
        const clone = button.cloneNode(true) as HTMLElement;
        document.body.appendChild(clone);
        const result = {
          panel: getComputedStyle(button).color,
          native: getComputedStyle(clone).color
        };
        clone.remove();
        return result;
      });
    expect(comparison.panel).toBe(comparison.native);
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
    expect(coverageTitle?.ratio).toBeLessThan(12);
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
        'History held from 16 Sep 2026 15:00:00 to 26 Sep 2026 15:00:00.',
        { exact: true }
      )
    ).toBeVisible();
    await captureMatrix(page, 'performance', 'custom-range', scheme);
  });
}
