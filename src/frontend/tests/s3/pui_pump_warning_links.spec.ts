import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { openHealth, signalTable } from './healthFixtures';
import { NATIVE_SIGNALS, openPerformance } from './performanceFixtures';

nativeBaseline();

for (const scheme of ['light', 'dark'] as const) {
  for (const status of ['open', 'resolved', 'suppressed'] as const) {
    test(`notification targets the exact ${status} alarm (${scheme})`, async ({
      page
    }, info) => {
      const linked = {
        pk: 72,
        title: 'Linked winding alarm',
        status
      };
      await openHealth(page, {
        scheme,
        entrySearch: '?anomaly=72',
        summary: {
          state: status === 'open' ? 'warning' : 'normal',
          active_anomaly_count: status === 'open' ? 1 : 0,
          anomaly_counts: {
            critical: 0,
            warning: status === 'open' ? 1 : 0,
            info: 0
          }
        },
        anomalies: status === 'open' ? [linked] : [],
        pastAnomalies:
          status === 'open'
            ? []
            : [
                ...Array.from({ length: 11 }, (_, index) => ({
                  pk: 100 + index,
                  status: 'resolved' as const,
                  title: `Earlier alarm ${index + 1}`
                })),
                linked
              ]
      });
      const target = page.locator('#machine-alarm-72');
      await expect(target).toHaveCount(1);
      await expect(target).toBeVisible();
      await expect(target).toBeFocused();
      await expect(target).toContainText('Linked winding alarm');
      await expect(target).toHaveCSS('outline-style', 'solid');
      await page.screenshot({
        path: info.outputPath(`alarm-link-${status}-${scheme}.png`)
      });
    });
  }

  test(`alarm readings color every cell, including selected and sticky cells (${scheme})`, async ({
    page
  }, info) => {
    await openHealth(page, {
      scheme,
      signals: [
        {
          id: 205,
          key: '/dex/PUMP7_PUMP_MOTOR_WINDING_TEMPERATURED5',
          name: 'Motor Winding Temperature 5',
          unit: 'degC',
          value: 149.8,
          state: 'critical',
          limits: { warn_max: 125, critical_max: 145 }
        },
        {
          id: 206,
          key: '/dex/PUMP7_PUMP_MOTOR_WINDING_TEMPERATURED6',
          name: 'Motor Winding Temperature 6',
          unit: 'degC',
          value: 152,
          state: 'critical',
          limits: { warn_max: 125, critical_max: 145 }
        },
        {
          id: 207,
          key: '/dex/PUMP7_PUMP_MOTOR_WINDING_TEMPERATURED7',
          name: 'Motor Winding Temperature 7',
          unit: 'degC',
          value: 130,
          state: 'warning',
          limits: { warn_max: 125, critical_max: 145 }
        },
        {
          id: 208,
          key: '/dex/PUMP7_PUMP_MOTOR_WINDING_TEMPERATURED8',
          name: 'Motor Winding Temperature 8',
          unit: 'degC',
          value: 100,
          state: 'normal',
          limits: { warn_max: 125, critical_max: 145 }
        }
      ]
    });
    const table = signalTable(page);
    const alarms = table.locator('tbody tr[data-alarm="true"]');
    await expect(alarms).toHaveCount(3);
    // Keep hover out of the table while comparing both themes' base fill.
    await page.mouse.move(0, 0);
    for (const row of await alarms.all()) {
      const backgrounds = await row
        .locator('td')
        .evaluateAll((cells) =>
          cells.map((cell) => getComputedStyle(cell).backgroundColor)
        );
      expect(new Set(backgrounds).size).toBe(1);
      expect(backgrounds[0]).not.toBe('rgba(0, 0, 0, 0)');
    }
    await page
      .getByRole('button', {
        name: 'Inspect signal: Motor Winding Temperature 5',
        exact: true
      })
      .click();
    await expect(table.locator('tr[data-selected="true"]')).toHaveAttribute(
      'data-alarm',
      'true'
    );
    await table.screenshot({
      path: info.outputPath(`alarm-rows-${scheme}.png`)
    });
  });

  test(`winding critical card reveals, focuses and highlights its chart (${scheme})`, async ({
    page
  }, info) => {
    const signals = NATIVE_SIGNALS.map((signal) =>
      signal.key.endsWith('WINDING_TEMPERATURED5')
        ? { ...signal, value: 149.8, state: 'critical' as const }
        : signal
    );
    await openPerformance(page, { scheme, signals, settleInitialRead: true });
    const target = page.locator('[id^="winding-chart-"]');
    await page
      .getByRole('button', {
        name: 'Collapse Motor winding temperature',
        exact: true
      })
      .click();
    await page.getByLabel('Find a channel', { exact: true }).fill('BYPASS');
    const card = page.getByRole('button', {
      name: 'Winding temperature',
      exact: true
    });
    await expect(card).toContainText('Critical');
    await card.focus();
    await page.keyboard.press('Enter');
    await expect(target).toBeVisible();
    await expect(target).toBeFocused();
    await expect(target).toHaveAttribute('data-highlighted', 'true');
    await expect(target.locator('.recharts-surface').first()).toBeVisible();
    const bounds = await target.boundingBox();
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.y).toBeLessThan(240);
    await page.screenshot({
      path: info.outputPath(`winding-link-${scheme}.png`)
    });
    await card.click();
    await expect(target).toBeFocused();
  });
}
