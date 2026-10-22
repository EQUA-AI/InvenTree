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
        await details.blur();
      }
      await page.mouse.move(0, 0);
      await expect(page.getByRole('tooltip')).toHaveCount(0);
    }

    await expect(stale.locator('[data-label="Value"]')).toHaveText(
      'Last known 812.5 cusec'
    );
    await expect(
      stale.locator('[data-label="State"]').getByText('Stale', { exact: true })
    ).toBeVisible();
    const observed = stale.locator('[data-label="Observed"] time');
    await expect(observed).toHaveText('10 min ago');
    await expect(observed).toHaveAttribute(
      'datetime',
      new Date(NOW - 600_000).toISOString()
    );
    await observed.hover();
    const observationTime = page.getByRole('tooltip', {
      name: /^Observed: 18 Oct 2026 /
    });
    await expect(observationTime).toContainText('18 Oct 2026');
    await expect(observationTime).toContainText('your clock');
    await page.mouse.move(0, 0);
    await page.keyboard.press('Escape');
    await expect(
      stale.locator('[data-label="State"]').getByText('Normal', { exact: true })
    ).toHaveCount(0);

    await expect(badQuality.locator('[data-label="Value"]')).toContainText(
      '95 °C'
    );
    const badQualityValue = badQuality.locator('[data-label="Value"]');
    await expect(
      badQualityValue.getByText('Bad quality', { exact: true })
    ).toBeVisible();
    await expect(badQualityValue.getByRole('button')).toHaveCount(0);
    const badQualityDetails = badQuality.getByRole('button', {
      name: 'Details for Winding temperature',
      exact: true
    });
    await badQualityDetails.focus();
    await expect(badQualityDetails).toBeFocused();
    const qualityReason = page.getByRole('tooltip').filter({
      hasText: 'Not assessed: quality is not good'
    });
    await expect(qualityReason).toContainText('95 °C');
    await expect(qualityReason).toContainText('Bad');
    await expect(badQualityDetails).toHaveAccessibleDescription(
      /95 °C.*Not assessed: quality is not good/
    );
    await page.keyboard.press('Escape');
    await expect(qualityReason).toBeHidden();
    await badQualityDetails.blur();
    await expect(badQuality.locator('[data-label="Quality"]')).toHaveText(
      'Bad'
    );
    const missingValue = missing.locator('[data-label="Value"]');
    const missingReason = missingValue.locator('p > span').first();
    expect(
      await missingReason.evaluate((node) =>
        [...node.childNodes]
          .filter((child) => child.nodeType === Node.TEXT_NODE)
          .map((child) => child.textContent)
          .join('')
          .trim()
      )
    ).toBe('—');
    await expect(missingReason).toContainText(
      'Value: No observation available'
    );
    await expect(missingReason).not.toHaveAttribute('tabindex');
    await expect(missingValue.getByRole('button')).toHaveCount(0);
    const missingDetails = missing.getByRole('button', {
      name: 'Details for Seal flush flow',
      exact: true
    });
    await missingDetails.focus();
    await expect(missingDetails).toBeFocused();
    const observationReason = page.getByRole('tooltip').filter({
      hasText: 'No observation available'
    });
    await expect(observationReason).toContainText(
      'Not assessed: no observation'
    );
    await expect(missingDetails).toHaveAccessibleDescription(
      /No observation available.*Not assessed: no observation/
    );
    await page.keyboard.press('Escape');
    await expect(observationReason).toBeHidden();
    await missingDetails.blur();
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
    const directory = resolve(
      process.env.AIMMS_UI_STATE_EVIDENCE_ROOT ??
        '../../data/devscratch/ui-r2/states'
    );
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
