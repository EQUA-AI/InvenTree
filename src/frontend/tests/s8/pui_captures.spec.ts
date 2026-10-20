import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { mockFaultHistory } from '../s3/machineWorkFixtures';
import { captureFromTop, mockEquipment } from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();
for (const screen of [
  'machines',
  'equipment-model',
  'equipment-schematic',
  'fault-history'
]) {
  for (const scheme of ['light', 'dark']) {
    for (const width of [1100, 1440, 1920, 390]) {
      test(`${screen} ${scheme} ${width}px capture`, async ({ page }, info) => {
        test.skip(
          info.project.name.endsWith('phone'),
          'Width matrix captured by desktop project'
        );
        test.setTimeout(120_000);
        await page.setViewportSize({
          width,
          height:
            width === 1100
              ? 800
              : width === 1920
                ? 1080
                : width === 390
                  ? 844
                  : 900
        });
        if (screen === 'machines') await mockExplorer(page);
        else if (screen === 'fault-history') await mockFaultHistory(page);
        else await mockEquipment(page);
        const phase = process.env.AIMMS_CAPTURE_PHASE || 'after';
        expect(phase).toMatch(/^[a-z0-9-]+$/);
        // Canonical inspected evidence is immutable on routine replays.
        // Explicit exports use a new named evidence batch.
        const batch = process.env.AIMMS_CAPTURE_BATCH;
        if (batch) expect(batch).toMatch(/^[a-z0-9-]+$/);
        const dir = batch
          ? resolve(
              '../../data/devscratch/r6-s8/S8-01',
              screen,
              'browser',
              batch
            )
          : info.outputPath('captures');
        await mkdir(dir, { recursive: true });
        const entry =
          screen === 'machines'
            ? '/machines/index/sites/?location=12'
            : screen === 'fault-history'
              ? '/machines/machine/18/fault-history/?occurrence=901'
              : `/machines/machine/18/equipment/${screen === 'equipment-schematic' ? '?view=schematic' : ''}`;
        await page.goto(
          `/playwright/s8-design.html?${new URLSearchParams({ screen, scheme, entry })}`,
          { waitUntil: 'domcontentloaded' }
        );
        await page
          .getByText(
            'S8 synthetic fixture · native panels · no live plant connection',
            { exact: true }
          )
          .waitFor({ timeout: 60_000 });
        await page
          .getByText('Loading native panel…', { exact: true })
          .waitFor({ state: 'hidden', timeout: 60_000 });
        // English must be readable; translation identifiers are invalid evidence.
        if (screen === 'machines') {
          await expect(
            page.getByRole('heading', { name: 'By location', exact: true })
          ).toBeVisible();
          await expect(page.getByRole('table')).toBeVisible({
            timeout: 60_000
          });
          await expect(page.getByTestId('machine-count-view')).toHaveText(
            '80',
            { timeout: 60_000 }
          );
        } else if (screen === 'equipment-model') {
          await expect(
            page.getByRole('radiogroup', { name: 'Model view mode' })
          ).toBeVisible();
          await expect(page.locator('[data-scene-phase="ready"]')).toBeVisible({
            timeout: 60_000
          });
        } else if (screen === 'equipment-schematic') {
          await expect(
            page.getByText('Pump unit: P1', { exact: true })
          ).toBeVisible();
        } else {
          await expect(
            page.getByRole('heading', { name: 'Occurrences', exact: true })
          ).toBeVisible();
          await expect(page.getByTestId('occurrence-evidence')).toBeVisible({
            timeout: 60_000
          });
        }
        await captureFromTop(page);
        expect(await page.evaluate(() => window.scrollY)).toBe(0);
        await page.screenshot({
          path: resolve(dir, `${phase}-${width}-${scheme}.png`),
          animations: 'disabled'
        });
        if (width === 390)
          await page.screenshot({
            path: resolve(dir, `${phase}-${width}-${scheme}-full.png`),
            fullPage: true,
            animations: 'disabled'
          });
        if (phase.startsWith('after'))
          expect(
            await page.evaluate(() => document.documentElement.scrollWidth)
          ).toBeLessThanOrEqual(width);
      });
    }
  }
}
