import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import type { MimicData } from '../../src/pages/assets/health/mimic/types';
import { fixture, observed } from '../mimic/layoutFixtures';
import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { components } from '../s2/partsFixtures';
import { openHealth } from '../s3/healthFixtures';
import { readable, svgEdge } from './contrast';
import { captureFromTop } from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();

/** Additional state evidence, separate from the owner's numbered 18 views. */
function mixedStation(unit: string | null): MimicData {
  const data = fixture(unit);
  const states = ['running', 'fault', 'stale', 'unknown', 'idle'];
  data.bays.forEach((bay, index) => {
    bay.state = states[index % states.length];
    bay.alarm = index === 0 ? 'critical' : index === 1 ? 'warning' : null;
    for (const point of Object.values(bay.points)) {
      if (point.unit === 'MW') point.value = bay.state === 'running' ? 1.25 : 0;
    }
  });
  const running = data.bays.filter((bay) => bay.state === 'running').length;
  for (const element of data.layout.elements) {
    if (element.id === 'station-status')
      data.station_points[element.pointer].value =
        data.layout.status_values?.[running ? 'running' : 'idle']?.[0] ?? null;
    if (element.id === 'pumps-running')
      data.station_points[element.pointer].value = running;
  }
  return data;
}

for (const scheme of ['light', 'dark'] as const) {
  for (const screen of ['station', 'pump', 'health', 'parts'] as const) {
    test(`round2 ${screen} abnormal evidence ${scheme}`, async ({
      page
    }, info) => {
      const phone = info.project.name.endsWith('phone');
      if (screen === 'station' || screen === 'pump') {
        await page.clock.setFixedTime(new Date(observed));
        await page.route(
          '**/api/machine-health/station/17/mimic/**',
          (route) => {
            const unit = new URL(route.request().url()).searchParams.get(
              'unit'
            );
            const data =
              screen === 'station' ? mixedStation(unit) : fixture(unit);
            if (screen === 'pump') {
              const bay = data.bays.find(
                (candidate) => candidate.key === 'P7'
              )!;
              bay.alarm = 'critical';
              const temperature = Object.values(data.points).find(
                (point) =>
                  point.part_code === 'PS-MOTOR' && point.unit === 'degC'
              )!;
              temperature.thresholds_configured = true;
              temperature.condition = 'unknown';
              temperature.value = 95;
              temperature.reason = 'bad_quality';
              temperature.quality = 'bad';
              bay.points[temperature.pointer] = temperature;
              for (const [part, condition, value] of [
                ['PS-AIR-COOLING', 'normal', 41],
                ['PS-THRUST-BRG', 'warning', 82],
                ['PS-WATER-COOLING', 'critical', 105]
              ] as const) {
                const assessed = Object.values(data.points).find(
                  (point) => point.part_code === part && point.unit === 'degC'
                )!;
                Object.assign(assessed, {
                  thresholds_configured: true,
                  condition,
                  value,
                  reason: null,
                  quality: 'good'
                });
                bay.points[assessed.pointer] = assessed;
              }
              data.alarms = [
                {
                  ...temperature,
                  machine: bay.machine,
                  anomaly: 901,
                  anomaly_status: 'open',
                  // An existing anomaly retains its severity while the
                  // current measurement cannot be assessed.
                  severity: 'critical'
                }
              ];
            }
            return route.fulfill({ json: data });
          }
        );
        await page.goto(
          `/tests/mimic/index.html?${new URLSearchParams({ scheme, ...(screen === 'pump' ? { pump: 'P7' } : {}) })}`
        );
        if (screen === 'station') {
          await expect(page.locator('g[data-bay]')).toHaveCount(14);
          await expect(
            page.locator(
              '[data-station-metric="station-status"] [data-reading-value]'
            )
          ).toHaveText('Running');
          await expect(
            page.locator(
              '[data-station-metric="pumps-running"] [data-reading-value]'
            )
          ).toHaveText('3');
          for (const state of [
            'Running',
            'Fault',
            'Stale',
            'Unknown',
            'Idle'
          ]) {
            await expect(
              page
                .locator('g[data-bay] text')
                .filter({ hasText: new RegExp(`^${state}$`) })
                .first()
            ).toBeVisible();
          }
          for (const alarm of ['critical', 'warning']) {
            await svgEdge(
              page
                .locator(`[data-alarm-marker="${alarm}"]`)
                .locator('path, polygon, circle')
                .first()
            );
            for (const text of await page
              .locator(`[data-alarm-marker="${alarm}"] text`)
              .all())
              await readable(text);
          }
          if (phone) {
            const viewport = page.getByRole('region', {
              name: 'Station pump bays'
            });
            await viewport.evaluate((node) => {
              node.scrollLeft = node.scrollWidth;
            });
            await expect(
              page.getByRole('button', { name: /^P14:/ })
            ).toBeInViewport();
            await viewport.evaluate((node) => {
              node.scrollLeft = 0;
            });
          }
        } else {
          const failed = page
            .getByRole('button', { name: /Bad quality/ })
            .last();
          await expect(failed).toBeVisible();
          await expect(
            page.locator('tr').filter({ has: failed })
          ).toContainText('95 °C');
          for (const condition of ['normal', 'warning', 'critical'])
            await expect(
              page.locator(`[data-part-markers] [data-verdict="${condition}"]`)
            ).toHaveCount(1);
          for (const marker of await page
            .locator('[data-part-markers] [data-part] > text')
            .all())
            await readable(marker);
        }
      } else if (screen === 'health') {
        await openHealth(page, {
          scheme,
          nativeTheme: true,
          compactChrome: true
        });
        await expect(
          page.getByText(/Normal · (2\/6|partial)/).first()
        ).toBeVisible();
        await expect(
          page.locator('tr').filter({ hasText: 'Motor DE vibration (raw)' })
        ).toContainText(/1,?834/);
      } else {
        await mockMachine(page);
        const rows = components(true).map((row) => ({
          ...row,
          reference_position: undefined,
          open_work: null,
          stock: null,
          fitted: undefined
        }));
        await page.route('**/api/assets/registry/18/components/**', (route) =>
          route.fulfill({ json: { count: rows.length, results: rows } })
        );
        await page.route('**/api/assets/parts/?*', (route) =>
          route.fulfill({ json: { count: 0, results: [], next: null } })
        );
        await page.goto(
          `/playwright/s0-native.html?${new URLSearchParams({ surface: 'machine-shell', compactChrome: 'true', nativeTheme: 'true', scheme, entry: '/machines/machine/18/parts/' })}`
        );
        await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
        for (const label of ['Open work', 'Catalogue stock'])
          await expect(
            page
              .getByRole('button', {
                name: `${label}: Read failed`,
                exact: true
              })
              .first()
          ).toBeVisible();
      }
      await captureFromTop(page);
      const directory = resolve('../../data/devscratch/ui-r2/states');
      await mkdir(directory, { recursive: true });
      await page.screenshot({
        path: resolve(
          directory,
          `${screen}-${scheme}-${phone ? 'phone' : 'desktop'}.png`
        ),
        fullPage: screen === 'pump',
        animations: 'disabled'
      });
      if (screen === 'health')
        await page
          .locator('table')
          .first()
          .screenshot({
            path: resolve(
              directory,
              `${screen}-readings-${scheme}-${phone ? 'phone' : 'desktop'}.png`
            ),
            animations: 'disabled'
          });
      if (scheme === 'dark' && !phone) {
        const target =
          screen === 'station'
            ? page.locator('g[data-bay="P1"]')
            : screen === 'pump'
              ? page.locator('[data-unit-callouts] button[data-part]').first()
              : screen === 'health'
                ? page.locator('table tbody tr button').first()
                : page.getByRole('button', { name: /Read failed/ }).first();
        await page.keyboard.press('Tab');
        await target.focus();
        await expect(target).toBeFocused();
        if (screen === 'station')
          await svgEdge(target.locator(':scope > rect').first());
        await page.screenshot({
          path: resolve(directory, `${screen}-${scheme}-desktop-focus.png`),
          animations: 'disabled'
        });
      }
    });
  }
}
