import { type Locator, expect, test } from '@playwright/test';
import type { FaultOccurrence } from '../../src/pages/assets/faults/occurrenceModel';
import { nativeBaseline } from '../s0/guards';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { mockFaultHistory, occurrences } from '../s3/machineWorkFixtures';
import { outlineEdge, readable } from './contrast';
import {
  captureFromTop,
  mockEquipment,
  openEquipment,
  openExplorer,
  openFaultHistory
} from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();
test.use({ timezoneId: 'UTC' });

async function touchTarget(control: Locator) {
  const box = (await control.boundingBox())!;
  expect(box.width).toBeGreaterThanOrEqual(44);
  expect(box.height).toBeGreaterThanOrEqual(44);
}

for (const scheme of ['light', 'dark'] as const) {
  for (const empty of [false, true]) {
    test(`failed occurrence refresh hides cached ${empty ? 'empty verdict' : 'rows and pagination'} until retry ${scheme}`, async ({
      page
    }, info) => {
      const rows = Array.from({ length: 26 }, (_, i) => ({
        ...(occurrences[0] as FaultOccurrence),
        id: 1000 + i
      }));
      const fixture = { empty, rows, listStatus: 200 };
      await mockFaultHistory(page, fixture);
      // openFaultHistory waits for a row: an empty successful read has no row.
      if (empty) {
        await page.goto(
          `/playwright/s8-design.html?${new URLSearchParams({ scheme, entry: '/machines/machine/18/fault-history/?fault_status=active' })}`
        );
      } else await openFaultHistory(page, { scheme });
      const panel = page.getByTestId('fault-occurrences');
      const emptyMessage = panel.getByText(
        'No occurrence is active on this machine now.',
        { exact: true }
      );
      if (empty) await expect(emptyMessage).toBeVisible();
      else {
        await expect(panel.locator('[data-occurrence]')).toHaveCount(25);
        await expect(
          panel.getByRole('navigation', { name: 'Occurrence pagination' })
        ).toBeVisible();
      }
      if (!empty) {
        await panel
          .locator('[data-occurrence]')
          .first()
          .getByRole('button', { name: 'Evidence', exact: true })
          .click();
        await expect(
          panel.getByRole('region', { name: 'Opened occurrence evidence' })
        ).toBeVisible();
      }
      fixture.listStatus = 503;
      await panel.getByRole('button', { name: 'Refresh', exact: true }).click();
      await expect(panel.getByRole('alert')).toContainText(
        'The history source is unavailable'
      );
      await expect(panel.locator('[data-occurrence]')).toHaveCount(0);
      await expect(
        panel.getByRole('region', { name: 'Opened occurrence evidence' })
      ).toHaveCount(0);
      await expect(
        panel.getByRole('navigation', { name: 'Occurrence pagination' })
      ).toHaveCount(0);
      await expect(emptyMessage).toHaveCount(0);
      await expect(panel.locator('output')).toHaveCount(0);
      await page.screenshot({
        path: info.outputPath(`occurrences-unavailable-${empty}-${scheme}.png`),
        fullPage: true
      });
      fixture.listStatus = 200;
      await panel
        .getByRole('button', { name: 'Retry occurrences', exact: true })
        .click();
      await expect(panel.getByRole('alert')).toHaveCount(0);
      if (empty) await expect(emptyMessage).toBeVisible();
      else await expect(panel.locator('[data-occurrence]')).toHaveCount(25);
    });
  }

  test(`condition totals remain visible and navigate to affected loaded rows ${scheme}`, async ({
    page
  }, info) => {
    await mockExplorer(page);
    await openExplorer(page, '/machines/index/sites/?location=12', scheme);
    const summary = page.getByRole('region', { name: 'Loaded page summary' });
    for (const [name, id] of [
      ['Critical · 4', 8],
      ['Warning · 5', 2],
      ['Read failed · 1', 3]
    ] as const) {
      const control = summary.getByRole('button', { name, exact: true });
      await expect(control).toBeVisible();
      if (info.project.name.endsWith('phone')) await touchTarget(control);
      await control.click();
      await expect(page.locator(`#machine-row-${id}`)).toBeFocused();
      await expect(page.locator(`#machine-row-${id}`)).toBeInViewport();
    }
    if (info.project.name.endsWith('phone')) {
      const criticalBox = (await summary
        .getByRole('button', { name: 'Critical · 4', exact: true })
        .boundingBox())!;
      const warningBox = (await summary
        .getByRole('button', { name: 'Warning · 5', exact: true })
        .boundingBox())!;
      expect(
        warningBox.x - criticalBox.x - criticalBox.width
      ).toBeGreaterThanOrEqual(8);
      const chooseBox = (await page
        .getByRole('button', { name: 'Choose location', exact: true })
        .boundingBox())!;
      const refreshBox = (await page
        .getByRole('button', { name: 'Refresh', exact: true })
        .first()
        .boundingBox())!;
      expect(
        refreshBox.x - chooseBox.x - chooseBox.width
      ).toBeGreaterThanOrEqual(8);
      await touchTarget(summary.getByRole('button', { name: /^In alarm/ }));
      for (const name of ['Choose location', 'Refresh', 'Location details']) {
        const control = page.getByRole('button', { name, exact: true }).first();
        await touchTarget(control);
      }
    }
    await captureFromTop(page);
    await page.screenshot({
      path: info.outputPath(`machine-counts-${scheme}.png`)
    });
    const headings = page.locator('[data-stylish-heading]:visible');
    expect(await headings.count()).toBeGreaterThan(0);
    for (const heading of await headings.all()) await readable(heading);
  });

  test(`pump status leads with sourced active alarms and a working alarm link ${scheme}`, async ({
    page
  }, info) => {
    const data = await mockEquipment(page);
    data.alarms[0].severity = 'critical';
    // These are outside the selected pump, not raised, suppressed or unknown:
    // none may inflate the active-alarm count.
    data.alarms.push(
      { ...data.alarms[0], machine: 99, anomaly: 999 },
      { ...data.alarms[0], anomaly: null }
    );
    await openEquipment(page, true, scheme);
    const summary = page.getByLabel('Active alarm summary');
    const coverage = page.locator('[data-limit-coverage]');
    const withoutLimits = Object.values(data.points).filter(
      (point) => point.thresholds_configured === false
    ).length;
    await expect(coverage).toContainText(`${withoutLimits} without limits`);
    await expect(coverage).not.toContainText('No active alarms');
    await expect(summary).toContainText(
      'Active alarms shown: 2 · Critical: 1 · Warning: 1'
    );
    if (info.project.name.endsWith('phone')) {
      await expect(
        summary.getByText('Active alarms shown: 2', { exact: true })
      ).toBeVisible();
      await expect(
        summary.getByText('Critical: 1 · Warning: 1', { exact: true })
      ).toBeVisible();
    }
    const drawing = page.getByRole('img', { name: 'Pump unit schematic' });
    expect((await summary.boundingBox())!.y).toBeLessThan(
      (await drawing.boundingBox())!.y
    );
    await captureFromTop(page);
    await expect(summary).toBeInViewport({ ratio: 1 });
    const link = summary.getByRole('link', {
      name: 'Open alarms',
      exact: true
    });
    if (info.project.name.endsWith('phone')) await touchTarget(link);
    await page.screenshot({
      path: info.outputPath(`pump-alarm-summary-${scheme}.png`)
    });
    await link.click();
    await expect(
      page.getByRole('heading', { name: 'Threshold alarms' })
    ).toBeFocused();
    await expect(
      page.getByRole('heading', { name: 'Threshold alarms' })
    ).toBeInViewport();
    await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
      route.fulfill({ status: 503, json: {} })
    );
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(
      page.getByText('Refresh to retry; cached values are hidden.')
    ).toBeVisible();
    await expect(summary).toHaveCount(0);
    await expect(coverage).toHaveCount(0);
  });

  test(`pump alarm summary names unavailable scope and unknown severity ${scheme}`, async ({
    page
  }) => {
    const data = await mockEquipment(page);
    data.alarms[0].severity = null;
    await openEquipment(page, true, scheme);
    const summary = page.getByLabel('Active alarm summary');
    const coverage = page.locator('[data-limit-coverage]');
    const withoutLimits = Object.values(data.points).filter(
      (point) => point.thresholds_configured === false
    ).length;
    await expect(coverage).toContainText(`${withoutLimits} without limits`);
    await expect(coverage).not.toContainText('No active alarms');
    await expect(summary).toContainText('Active alarms shown: 2 · Warning: 1');
    await expect(summary).toContainText('Severity unavailable: 1');
    await expect(summary).not.toContainText('Critical: 0');
    data.alarms = [];
    data.bays[0].alarm = 'critical';
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(summary).toContainText(
      'Active alarm · Critical · Count unavailable'
    );
    await expect(summary).not.toContainText('Active alarms shown: 0');
    await expect(coverage).not.toContainText('No active alarms');
    await expect(
      page.getByText('Active alarm details unavailable.', { exact: true })
    ).toBeVisible();
    data.bays[0].alarm = null;
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(summary).toHaveCount(0);
    await expect(coverage).toContainText('No active alarms');
    await expect(coverage).toContainText(`${withoutLimits} without limits`);
    await expect(
      page.getByRole('heading', { name: 'Threshold alarms' })
    ).toHaveCount(0);
    data.bays[0].machine = undefined;
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(summary).toContainText(
      'Active alarms unavailable · pump mapping not supplied'
    );
    await expect(summary).not.toContainText('Active alarms shown: 0');
    await expect(coverage).not.toContainText('No active alarms');
    await expect(
      page.getByText('Alarm scope unavailable for this pump.', { exact: true })
    ).toBeVisible();
  });

  test(`schematic keyboard focus has a separate contrasting ring across conditions ${scheme}`, async ({
    page
  }, info) => {
    const data = await mockEquipment(page);
    const point = Object.values(data.points).find(
      (point) => point.unit === 'kW'
    )!;
    point.part_code = 'PS-MOTOR';
    point.thresholds_configured = true;
    await openEquipment(page, true, scheme);
    const button = page.locator('button[data-part="PS-MOTOR"]');
    const ringColours = new Set<string>();
    for (const condition of ['normal', 'warning', 'critical', 'unknown']) {
      point.condition = condition;
      await page.getByRole('button', { name: 'Refresh', exact: true }).click();
      await expect(button).toHaveAttribute('data-verdict', condition);
      await page.keyboard.press('Tab');
      await button.focus();
      await expect(button).toBeFocused();
      const focused = await button.evaluate((node) => ({
        width: getComputedStyle(node).outlineWidth,
        style: getComputedStyle(node).outlineStyle,
        color: getComputedStyle(node).outlineColor
      }));
      expect(focused.width).toBe('3px');
      expect(focused.style).toBe('solid');
      ringColours.add(focused.color);
      await outlineEdge(button);
      await page.mouse.move(0, 0);
      await expect(button).toHaveCSS('outline-width', '3px');
    }
    expect(ringColours.size).toBe(1);
    await button.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath(`schematic-focus-${scheme}.png`)
    });
    await button.press('Tab');
    await expect(button).not.toBeFocused();
    await expect(button).not.toHaveCSS('outline-style', 'solid');
  });

  test(`occurrence observations preserve both endpoints and phone evidence hit area ${scheme}`, async ({
    page
  }, info) => {
    await mockFaultHistory(page);
    await openFaultHistory(page, { scheme });
    const row = page.locator('[data-occurrence="902"]');
    const sameInstant = page.locator('[data-occurrence="900"]');
    if (info.project.name.endsWith('phone')) {
      await expect(row).toContainText('First observed');
      await expect(row).toContainText('Last observed');
      await expect(row.getByText('Occurred', { exact: true })).toHaveCount(0);
      await expect(sameInstant).toContainText('First observed');
      await expect(sameInstant).not.toContainText('Last observed');
      await touchTarget(
        row.getByRole('button', { name: 'Evidence', exact: true })
      );
    }
    await expect(row).toContainText('19 Oct 2026 06:10');
    await expect(row).toContainText('19 Oct 2026 09:40');
    for (const heading of await page
      .locator('[data-stylish-heading]:visible')
      .all())
      await readable(heading);
    await row.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath(`occurrence-span-${scheme}.png`)
    });
  });
}
