import { type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { openPerformance, signal } from './performanceFixtures';
import { BAYS, type BaySpec, openStation } from './stationFixtures';

nativeBaseline();

/**
 * R6 P11 S3-07 / A19.7 / A19.15: every chart, family card, difference and
 * sum that puts readings on one scale checks their units first. Unlike units
 * are drawn apart and said to be; nothing is rescaled.
 */

const card = (page: Page, title: string) =>
  page
    .locator('.mantine-Paper-root')
    .filter({ has: page.getByText(title, { exact: true }) })
    .first();

const power = signal('/dex/PUMP1_ACTIVE_POWER', 8.42, 'MW', 'Active Power');
const speed = signal('/dex/PUMP1_SPEED', 745, 'rpm', 'Motor Shaft Speed');
const current = signal('/dex/PUMP1_PUMP_CURRENT_AVG', 612, 'A', 'Current');
const lineVoltage = (unit: string, value: number) =>
  signal('/dex/PUMP1_PUMP_LINE_TO_LINE_VOLTAGE', value, unit, 'Line Voltage');
const phaseVoltages = ['RY', 'YB', 'BR'].map((side) =>
  signal(`/dex/PUMP1_PUMP_${side}_VOLTAGE`, 6580, 'V', `${side} Voltage`)
);

test('an axis that would hold two units draws the second on its own trend', async ({
  page
}, testInfo) => {
  for (const scheme of ['light', 'dark'] as const) {
    await openPerformance(page, {
      signals: [
        power,
        speed,
        current,
        lineVoltage('kV', 6.6),
        ...phaseVoltages
      ],
      scheme
    });
    const supply = card(page, 'Current and voltage');
    await expect(supply.locator('[data-units-split]')).toHaveAttribute(
      'data-units-split',
      '1'
    );
    // Phase voltages come first in the resolved order and keep the right
    // axis; the line-to-line voltage in kV gets its own trend.
    await expect(supply).toContainText(
      'Drawn apart: Line-to-line voltage in kV, not V.'
    );
    // The main chart's axes name the units they actually carry.
    await expect(
      supply.locator('[data-axis-title="right"]').first()
    ).toHaveText('V');
    await expect(supply.locator('[data-axis-title="left"]').first()).toHaveText(
      'A'
    );
    await expect(supply.locator('.recharts-surface')).toHaveCount(2);
    await supply.screenshot({
      path: testInfo.outputPath(`supply-split-${scheme}.png`),
      animations: 'disabled'
    });
  }
  // One unit per axis: drawn as the native single chart.
  await openPerformance(page, {
    signals: [power, speed, current, lineVoltage('V', 6600), ...phaseVoltages]
  });
  const supply = card(page, 'Current and voltage');
  await expect(supply.locator('[data-units-split]')).toHaveCount(0);
  await expect(supply.locator('.recharts-surface')).toHaveCount(1);
});

test('valve positions in two units are not drawn on one right axis', async ({
  page
}) => {
  await openPerformance(page, {
    signals: [
      power,
      speed,
      signal('/dex/PUMP1_DISCHARGE_PRESSURE', 12.4, 'mH2O', 'Pressure'),
      signal('/dex/PUMP1_EOPD_VALVE_POS_PROCESS_VALUE', 100, 'percent', 'EOPD'),
      signal('/dex/PUMP1_HOPD_VALVE_POS_PROCESS_VALUE', 412, 'mm', 'HOPD')
    ]
  });
  const valves = card(page, 'Discharge pressure and valve position');
  await expect(valves).toContainText(
    'Drawn apart: HOPD valve position in mm, not percent.'
  );
  await expect(valves.locator('.recharts-surface')).toHaveCount(2);
});

test('a sensor family in two units gets a card per unit, and the hottest-sensor tile ranks one unit', async ({
  page
}, testInfo) => {
  const winding = (index: number, unit: string, value: number) =>
    signal(
      `/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED${index}`,
      value,
      unit,
      `Winding ${index}`,
      { limits: { warn_max: 125, critical_max: 145 } }
    );
  for (const scheme of ['light', 'dark'] as const) {
    await openPerformance(page, {
      signals: [
        power,
        speed,
        winding(1, 'degC', 80),
        winding(2, 'degC', 82),
        winding(3, 'degC', 84),
        // degF and degC cannot be ranked as plain numbers; the old tile did.
        winding(4, 'degF', 190)
      ],
      scheme
    });
    const section = page.locator('[data-section="Motor winding temperature"]');
    await expect(
      section.getByText('Stator winding temperature (degC)', { exact: true })
    ).toBeVisible();
    await expect(
      section.getByText('Stator winding temperature (degF)', { exact: true })
    ).toBeVisible();
    await expect(
      section
        .getByText(
          "This family reports in degC, degF; each unit has its own card, so no reading is compared on another's scale."
        )
        .first()
    ).toBeVisible();
    const tile = page.locator('[data-kpi="winding"]');
    await expect(tile).toContainText('84');
    await expect(tile).toContainText('Highest of 3: Winding 3');
    await expect(tile).toContainText('1 in degF not compared');
    await expect(tile).not.toContainText('190');
    await section.screenshot({
      path: testInfo.outputPath(`winding-split-${scheme}.png`),
      animations: 'disabled'
    });
  }
});

test('a temperature rise is calculated only where both sensors share a unit', async ({
  page
}) => {
  await openPerformance(page, {
    signals: [
      power,
      signal('/dex/PUMP1_PUMP_COOLING_WATER_INLET_TEMP1', 29, 'degC', 'In 1'),
      signal('/dex/PUMP1_PUMP_COOLING_WATER_OUTLET_TEMP1', 93, 'degF', 'Out 1'),
      signal('/dex/PUMP1_PUMP_COOLING_WATER_INLET_TEMP2', 29.1, 'degC', 'In 2'),
      signal('/dex/PUMP1_PUMP_COOLING_WATER_OUTLET_TEMP2', 34, 'degC', 'Out 2')
    ]
  });
  const rise = card(page, 'Cooling water rise (outlet − inlet)');
  await expect(rise).toContainText(
    'Not calculated, because the paired units differ: Cooling water outlet 1 (degF) − Cooling water inlet 1 (degC).'
  );
  await expect(rise).toContainText(/ΔT 2: /);
  await expect(rise).not.toContainText(/ΔT 1: /);
});

test('a bay in another unit is drawn apart, left out of the plant sum and labelled in the table', async ({
  page
}, testInfo) => {
  const bays: BaySpec[] = BAYS.map((bay) =>
    bay.key === 'P3' ? { ...bay, power: 7950, powerUnit: 'kW' } : bay
  );
  for (const scheme of ['light', 'dark'] as const) {
    await openStation(page, { bays, scheme });
    const comparison = card(page, 'Active power by bay');
    await expect(comparison).toContainText(
      'Drawn apart: Pump 3 in kW, not MW.'
    );
    await expect(comparison.locator('.recharts-surface')).toHaveCount(2);
    const total = page.locator('[data-kpi="plant-power"]');
    await expect(total).toContainText("sum of 3 of 4 bays' newest readings");
    await expect(total).toContainText('not summed, unit differs: Pump 3 (kW)');
    const row = page.locator('tbody tr').filter({ hasText: 'Pump 3' });
    await expect(row.locator('td').nth(2)).toContainText('kW');
    await expect(
      page
        .locator('tbody tr')
        .filter({ hasText: 'Pump 1' })
        .locator('td')
        .nth(2)
    ).not.toContainText('W');
    await expect(page.locator('thead th').nth(2)).toHaveText(
      'Active power (MW)'
    );
    await page.screenshot({
      path: testInfo.outputPath(`station-units-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
  }
});
