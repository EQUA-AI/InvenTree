import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { mockFaultHistory } from '../s3/machineWorkFixtures';
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
for (const state of [
  'station-stale',
  'station-empty',
  'schematic-stale',
  'schematic-polling-error',
  'schematic-unknown',
  'alarm-active',
  'alarm-acknowledged',
  'alarm-cleared'
]) {
  for (const scheme of ['light', 'dark'] as const)
    for (const width of [1100, 1440, 1920, 390]) {
      test(`${state} ${scheme} ${width}px state capture`, async ({
        page
      }, info) => {
        test.skip(
          info.project.name.endsWith('phone'),
          'Width matrix uses desktop project'
        );
        await page.setViewportSize({
          width,
          height: width === 390 ? 844 : width === 1920 ? 1080 : 900
        });
        let screen: string;
        if (state.startsWith('station-')) {
          screen = 'machines';
          const fixture = await mockExplorer(page);
          if (state === 'station-empty') {
            fixture.state.totalLimit = 0;
            fixture.location.counts.direct_machines = 0;
            fixture.location.counts.total_machines = 0;
            fixture.location.counts.direct_open_work_orders = 0;
            fixture.location.counts.total_open_work_orders = 0;
          }
          await openExplorer(
            page,
            '/machines/index/sites/?location=12',
            scheme
          );
          await expect(
            page.getByRole('region', { name: 'Loaded page summary' })
          ).toContainText(
            state === 'station-empty'
              ? '0 / 0'
              : '10 loaded machines report stale signals'
          );
        } else if (state.startsWith('schematic-')) {
          screen = 'equipment-schematic';
          const data = await mockEquipment(
            page,
            state === 'schematic-stale' ? 'stale' : 'unknown'
          );
          if (state === 'schematic-polling-error')
            data.last_error_code = 'connection_failed';
          await openEquipment(page, true, scheme);
          await expect(
            page.getByText('Communication status: unavailable')
          ).toBeVisible();
          if (state === 'schematic-polling-error')
            await expect(
              page.getByText('Last station polling error: connection_failed')
            ).toBeVisible();
          if (state === 'schematic-stale')
            await expect(page.getByText('7.5 MW', { exact: true })).toHaveCount(
              0
            );
          await expect(page.locator('tr[data-anomaly="902"]')).toContainText(
            'Acknowledged · warning'
          );
          await expect(page.locator('tr[data-anomaly="904"]')).toContainText(
            'Alarm state unavailable'
          );
        } else {
          screen = 'fault-history';
          await mockFaultHistory(page);
          const id =
            state === 'alarm-active'
              ? 902
              : state === 'alarm-acknowledged'
                ? 900
                : 901;
          await openFaultHistory(page, { scheme, query: `?occurrence=${id}` });
          const row = page.locator(`[data-occurrence="${id}"]`).first();
          await expect(row).toContainText(
            state === 'alarm-active'
              ? 'Active'
              : state === 'alarm-acknowledged'
                ? 'Acknowledged, not resolved'
                : 'Reading back inside its limits.'
          );
        }
        const batch = process.env.AIMMS_CAPTURE_BATCH;
        if (batch) expect(batch).toMatch(/^[a-z0-9-]+$/);
        const dir = batch
          ? resolve(
              '../../data/devscratch/r6-s8/S8-01',
              screen,
              'browser',
              batch,
              'states'
            )
          : info.outputPath('captures');
        await mkdir(dir, { recursive: true });
        await captureFromTop(page);
        await page.screenshot({
          path: resolve(dir, `${state}-${width}-${scheme}.png`),
          animations: 'disabled'
        });
        if (width === 390)
          await page.screenshot({
            path: resolve(dir, `${state}-${width}-${scheme}-full.png`),
            fullPage: true,
            animations: 'disabled'
          });
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth)
        ).toBeLessThanOrEqual(width);
      });
    }
}

for (const scheme of ['light', 'dark'] as const)
  for (const width of [1100, 390]) {
    test(`selected reference bearing ${scheme} ${width}px capture`, async ({
      page
    }, info) => {
      test.skip(
        info.project.name.endsWith('phone'),
        'Explicit widths use desktop project'
      );
      await page.setViewportSize({ width, height: width === 390 ? 844 : 800 });
      await mockEquipment(page);
      await openEquipment(page, false, scheme);
      await page.locator('[data-position="bearing"]').click();
      await page
        .getByRole('radiogroup', { name: 'Model view mode' })
        .getByText('Cutaway', { exact: true })
        .click();
      await expect(
        page.getByLabel('Selected position', { exact: true })
      ).toContainText('Motor drive-end bearing');
      await expect(
        page.getByText('DE bearing temperature', { exact: true })
      ).toBeVisible();
      const batch = process.env.AIMMS_CAPTURE_BATCH;
      if (batch) expect(batch).toMatch(/^[a-z0-9-]+$/);
      const dir = batch
        ? resolve(
            '../../data/devscratch/r6-s8/S8-01/equipment-model/browser',
            batch,
            'states'
          )
        : info.outputPath('captures');
      await mkdir(dir, { recursive: true });
      await page.screenshot({
        path: resolve(dir, `selected-bearing-${width}-${scheme}-full.png`),
        fullPage: true,
        animations: 'disabled'
      });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth)
      ).toBeLessThanOrEqual(width);
    });
  }
