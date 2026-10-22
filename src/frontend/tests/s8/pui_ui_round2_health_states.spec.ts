import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  type HealthSignalSpec,
  NOW,
  openHealth,
  signalTable
} from '../s3/healthFixtures';
import { captureFromTop } from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();

const signals: readonly HealthSignalSpec[] = [
  {
    id: 201,
    key: '/pd/P1/dv',
    name: 'Discharge flow',
    unit: 'cusec',
    kind: 'flow',
    value: 812.5,
    state: 'normal',
    quality: 'good',
    stale: true,
    observedAt: new Date(NOW - 600_000).toISOString(),
    freshnessSeconds: 60,
    limits: { warn_min: 600, critical_min: 400 }
  },
  {
    id: 206,
    key: '/dex/PUMP1_WINDING_TEMPERATURE',
    name: 'Winding temperature',
    unit: 'degC',
    kind: 'temperature',
    value: 95,
    state: 'unknown',
    quality: 'bad',
    limits: { warn_max: 125, critical_max: 145 }
  },
  {
    id: 204,
    key: '/dex/PUMP1_SEAL_FLUSH_FLOW',
    name: 'Seal flush flow',
    unit: 'l/min',
    kind: 'flow',
    value: null,
    state: 'unknown'
  },
  {
    id: 202,
    key: '/dex/PUMP1_BYPASS_FLOW',
    name: 'Bypass flow',
    unit: 'm3/h',
    kind: 'flow',
    value: 0,
    state: 'normal',
    limits: { warn_max: 10, critical_max: 20 }
  }
];

for (const scheme of ['light', 'dark'] as const) {
  test(`Health stale, bad-quality, missing and failed reads stay distinct ${scheme}`, async ({
    page
  }, info) => {
    const phone = info.project.name.endsWith('phone');
    const requests = await openHealth(page, {
      scheme,
      nativeTheme: true,
      compactChrome: true,
      signals,
      batchStatus: 503
    });
    const table = signalTable(page);
    const row = (name: string) =>
      table.getByRole('row').filter({
        has: page.getByRole('button', {
          name: `Inspect signal: ${name}`,
          exact: true
        })
      });
    const stale = row('Discharge flow');
    const badQuality = row('Winding temperature');
    const missing = row('Seal flush flow');
    const zero = row('Bypass flow');

    if (phone) {
      for (const name of ['Discharge flow', 'Winding temperature']) {
        const details = table.getByRole('button', {
          name: `Details for ${name}`,
          exact: true
        });
        await details.click();
        await expect(details).toHaveAttribute('aria-expanded', 'true');
      }
    }

    await expect(stale.locator('[data-label="Value"]')).toHaveText(
      'Last known 812.5 cusec'
    );
    await expect(
      stale.locator('[data-label="State"]').getByText('Stale', { exact: true })
    ).toBeVisible();
    await expect(stale.locator('[data-label="Observed"]')).toContainText(
      'Stale · 10 min old · freshness budget 60 s'
    );
    await expect(
      stale.locator('[data-label="State"]').getByText('Normal', { exact: true })
    ).toHaveCount(0);

    await expect(badQuality.locator('[data-label="Value"]')).toContainText(
      '95 °C'
    );
    await expect(
      badQuality.getByRole('button', {
        name: 'Value: Bad quality',
        exact: true
      })
    ).toBeVisible();
    await expect(badQuality.locator('[data-label="Quality"]')).toHaveText(
      'Bad'
    );
    const missingValue = missing.locator('[data-label="Value"]');
    await expect(
      missingValue.getByRole('button', {
        name: 'Value: No observation available',
        exact: true
      })
    ).toHaveText('—');
    await expect(missingValue).not.toContainText('Read failed');
    await expect(zero.locator('[data-label="Value"]')).toHaveText('0 m³/h');

    await expect.poll(() => requests.batch.length).toBeGreaterThan(0);
    await expect(
      stale
        .locator('[data-label="Trend"]')
        .getByText('Read failed', { exact: true })
    ).toBeVisible();
    await expect(
      badQuality
        .locator('[data-label="Trend"]')
        .getByText('Read failed', { exact: true })
    ).toBeVisible();
    await expect(
      table.getByText('No samples in window', { exact: true })
    ).toHaveCount(0);

    await page.mouse.move(0, 0);
    await page.keyboard.press('Escape');
    const directory = resolve('../../data/devscratch/ui-r2/states');
    await mkdir(directory, { recursive: true });
    await captureFromTop(page);
    await page.screenshot({
      path: resolve(
        directory,
        `health-failure-${scheme}-${phone ? 'phone' : 'desktop'}.png`
      ),
      fullPage: true,
      animations: 'disabled'
    });
  });
}
