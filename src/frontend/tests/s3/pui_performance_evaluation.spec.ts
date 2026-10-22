import { type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import {
  type SignalSpec,
  openPerformance,
  signal
} from './performanceFixtures';

nativeBaseline();

const power = signal('/dex/PUMP1_ACTIVE_POWER', 8.42, 'MW', 'Active Power');
const speed = signal('/dex/PUMP1_SPEED', 745, 'rpm', 'Motor Shaft Speed');
const factor = signal('/dex/PUMP1_PUMP_POWERFATCOR', 0.94, '', 'Power Factor');
const winding = (index: number, extra: Partial<SignalSpec> = {}) =>
  signal(
    `/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED${index}`,
    80 + index,
    'degC',
    `Motor Winding Temperature ${index}`,
    { limits: { warn_max: 125, critical_max: 145 }, ...extra }
  );
const reactive = (unit: string) =>
  signal('/dex/PUMP1_PUMP_REACTIVE_POWER', 2.1, unit, 'Reactive Power');

const strip = (page: Page) =>
  page.locator('.mantine-Paper-root').filter({ hasText: 'Exceptions' });

test('a machine with no configured limits is never called calm', async ({
  page
}) => {
  await openPerformance(page, { signals: [power, speed] });
  await expect(
    strip(page).getByText('No configured limits to evaluate')
  ).toBeVisible();
  await expect(page.getByText('No exceptions')).toHaveCount(0);
  await page.keyboard.press('Tab');
  await strip(page)
    .getByRole('button', {
      name: 'Exceptions: assessment coverage',
      exact: true
    })
    .focus();
  await expect(page.getByRole('tooltip')).toHaveText(
    '0 of 2 evaluated; 0 have configured limits'
  );
});

test('limits that cannot be judged now say evaluation is unavailable', async ({
  page
}) => {
  await openPerformance(page, {
    harness: 's3',
    signals: [
      power,
      winding(1, { stale: true, state: 'unknown' }),
      winding(2, { stale: true, state: 'unknown' })
    ]
  });
  await expect(strip(page).getByText('Evaluation unavailable')).toBeVisible();
  await expect(strip(page).getByText('2 of 3 signals stale')).toBeVisible();
  await expect(page.getByText('No exceptions')).toHaveCount(0);
  await page.keyboard.press('Tab');
  await strip(page)
    .getByRole('button', {
      name: 'Exceptions: assessment coverage',
      exact: true
    })
    .focus();
  await expect(page.getByRole('tooltip')).toHaveText(
    '0 of 3 evaluated; 2 have configured limits'
  );
});

test('calm is claimed only when every judged reading is in limits, and says how many were judged', async ({
  page
}, testInfo) => {
  for (const scheme of ['light', 'dark'] as const) {
    await openPerformance(page, {
      harness: 's3',
      signals: [power, speed, winding(1), winding(2), winding(3)],
      scheme
    });
    await expect(strip(page).getByText('No exceptions')).toBeVisible();
    await page.keyboard.press('Tab');
    await strip(page)
      .getByRole('button', {
        name: 'Exceptions: assessment coverage',
        exact: true
      })
      .focus();
    await expect(page.getByRole('tooltip')).toHaveText(
      '3 of 5 evaluated; 3 have configured limits'
    );
    await strip(page)
      .getByRole('button', {
        name: 'Exceptions: assessment coverage',
        exact: true
      })
      .blur();
    await expect(page.getByRole('tooltip')).toBeHidden();
    await strip(page).screenshot({
      path: testInfo.outputPath(`unusual-calm-${scheme}.png`),
      animations: 'disabled'
    });
  }
  // A reading outside its limits is still named, and calm is not claimed.
  await openPerformance(page, {
    harness: 's3',
    signals: [power, winding(1, { state: 'warning' }), winding(2)]
  });
  await expect(strip(page).getByText(/1 warning: /)).toBeVisible();
  await expect(page.getByText('No exceptions')).toHaveCount(0);
});

test('reactive power shares the power axis only at the same magnitude, and power factor keeps its own', async ({
  page
}, testInfo) => {
  const card = (title: string) =>
    page
      .locator('[data-section="Electrical"] .mantine-Paper-root')
      .filter({ has: page.getByText(title, { exact: true }) })
      .first();
  const axis = (title: string, side: 'left' | 'right') =>
    card(title).locator(`[data-axis-title="${side}"]`).first();

  for (const scheme of ['light', 'dark'] as const) {
    await openPerformance(page, {
      harness: 's3',
      signals: [power, reactive('MVar'), factor, speed],
      scheme
    });
    const powerCard = 'Power';
    await expect(axis(powerCard, 'left')).toHaveText('MW / MVar');
    await expect(axis('Power factor', 'left')).toHaveText('Power factor');
    await expect(axis(powerCard, 'right')).toHaveCount(0);
    await expect(card('Power factor')).toBeVisible();
    await expect(page.getByText('Reactive power', { exact: true })).toHaveCount(
      // Its toggle chip in the power card; no separate card.
      1
    );
    await card(powerCard).screenshot({
      path: testInfo.outputPath(`electrical-shared-${scheme}.png`),
      animations: 'disabled'
    });
  }

  // kvar beside MW is a different magnitude: drawn apart, never rescaled.
  await openPerformance(page, {
    harness: 's3',
    signals: [power, reactive('kvar'), factor, speed]
  });
  await expect(axis('Power', 'left')).toHaveText('MW');
  await expect(axis('Power factor', 'left')).toHaveText('Power factor');
  await expect(axis('Power', 'right')).toHaveCount(0);
  const own = card('Reactive power');
  await expect(own).toContainText(
    'Drawn apart from active power: their units (MW and kvar) are not stated at the same magnitude.'
  );
  await expect(own.locator('[data-axis-title="left"]').first()).toHaveText(
    'kvar'
  );
  await own.screenshot({
    path: testInfo.outputPath('electrical-apart.png'),
    animations: 'disabled'
  });
});
