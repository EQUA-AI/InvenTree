import { writeFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import type { FaultOccurrence } from '../../src/pages/assets/faults/occurrenceModel';
import { nativeBaseline } from '../s0/guards';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { mockPositions, positionsFixture } from '../s2/equipmentFixtures';
import { mockFaultHistory, occurrences } from '../s3/machineWorkFixtures';
import { captureEvidence } from './captureEvidence';
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
test.use({ timezoneId: 'Asia/Riyadh' });
const states = [
  'base-machines',
  'base-model',
  'base-schematic',
  'base-fault-history',
  'station-stale',
  'station-empty',
  'station-condition-unavailable',
  'machines-info-alarm',
  'schematic-stale',
  'schematic-unknown',
  'schematic-polling-error',
  'schematic-polling-disabled',
  'schematic-query-error',
  'schematic-shifted-history',
  '3d-assembled-hidden-markers',
  '3d-cutaway-hidden-markers',
  '3d-no-slot',
  '3d-unbound',
  '3d-selected-bearing',
  'alarm-active',
  'alarm-acknowledged',
  'alarm-resolved',
  'alarm-dismissed',
  'snapshot-outside-span',
  'alarm-table-viewer',
  'alarm-table-acknowledger'
];
for (const state of states)
  for (const scheme of ['light', 'dark'] as const)
    test(`${state} ${scheme} full viewport matrix`, async ({ page }, info) => {
      test.skip(
        info.project.name.endsWith('phone'),
        'One matrix command resizes the native page through desktop and phone'
      );
      test.setTimeout(180000);
      let screen: string;
      if (state.includes('machines') || state.startsWith('station-')) {
        screen = 'machines';
        const fixture = await mockExplorer(page);
        if (state === 'station-empty') {
          fixture.state.totalLimit = 0;
          fixture.location.counts = {
            direct_machines: 0,
            total_machines: 0,
            direct_open_work_orders: 0,
            total_open_work_orders: 0
          };
        }
        if (state === 'machines-info-alarm')
          delete (fixture.machines[3] as { open_warning_alarms?: number })
            .open_warning_alarms;
        if (state === 'station-condition-unavailable')
          for (const machine of fixture.machines) machine.condition = null;
        if (state === 'station-stale')
          for (const machine of fixture.machines) {
            machine.condition = {
              state: 'offline',
              configured: true,
              signal_count: 6,
              stale_signal_count: 6,
              degraded_data: true
            };
            machine.open_alarms = 0;
            machine.open_critical_alarms = 0;
            machine.open_warning_alarms = 0;
          }
        await openExplorer(page, '/machines/index/sites/?location=12', scheme);
        if (state === 'station-empty') {
          await expect(
            page.getByRole('heading', {
              name: 'No machines at this location',
              exact: true
            })
          ).toBeVisible();
          await expect(page.getByRole('columnheader')).toHaveCount(0);
          await expect(
            page.getByRole('region', { name: 'Loaded page summary' })
          ).toHaveCount(0);
        } else {
          await expect(
            page.getByRole('region', { name: 'Loaded page summary' })
          ).toContainText('25 of 79');
          await expect(
            page.getByRole('region', { name: 'Loaded page summary' })
          ).not.toContainText('This page');
          if (state === 'station-condition-unavailable') {
            const summary = page.getByRole('region', {
              name: 'Loaded page summary'
            });
            await expect(
              summary.getByTestId('machine-page-count')
            ).toHaveAttribute(
              'aria-label',
              /Stale-signal coverage unavailable for 25 machines/
            );
            await expect(summary).not.toContainText(
              'Stale signals on 0 machines'
            );
          }
          if (state === 'station-stale')
            await expect(
              page.getByTestId('machine-page-count')
            ).toHaveAttribute('aria-label', /Stale signals on 25 machines/);
        }
      } else if (
        (state.startsWith('alarm-') && !state.startsWith('alarm-table')) ||
        state === 'snapshot-outside-span' ||
        state === 'base-fault-history'
      ) {
        screen = 'fault-history';
        let id =
          state === 'alarm-active'
            ? 902
            : state === 'alarm-acknowledged'
              ? 900
              : 901;
        let rows = structuredClone(occurrences) as FaultOccurrence[];
        if (state === 'alarm-dismissed') {
          id = 899;
          rows = [
            {
              ...rows[2],
              id,
              status: 'suppressed',
              active: false,
              metrics: {
                dismissed: {
                  by: 11,
                  at: '2026-10-20T07:58:00Z',
                  note: 'Synthetic review: source mapping was incorrect.'
                }
              }
            }
          ];
        }
        if (state === 'snapshot-outside-span') {
          const row = rows.find((row) => row.id === 901)!;
          row.snapshots[0].window_start = '2025-07-27T02:14:05Z';
          row.snapshots[0].window_end = '2025-07-27T02:14:05Z';
        }
        await mockFaultHistory(page, { rows });
        await openFaultHistory(page, {
          scheme,
          query: state === 'base-fault-history' ? '' : `?occurrence=${id}`
        });
        if (state !== 'base-fault-history') {
          const evidence = page.getByTestId('occurrence-evidence');
          await expect(evidence).toBeVisible();
          await expect(
            evidence.locator('[data-alarm-status]').first()
          ).toHaveText(
            state === 'alarm-active'
              ? 'Active · not acknowledged'
              : state === 'alarm-acknowledged'
                ? 'Active · acknowledged'
                : state === 'alarm-dismissed'
                  ? 'Dismissed'
                  : 'Resolved'
          );
          if (state === 'snapshot-outside-span') {
            await expect(evidence).toContainText('Outside occurrence span');
            await expect(evidence).toContainText(
              'Sample age at capture: 442 d'
            );
          }
        }
      } else {
        const schematic =
          state.startsWith('schematic-') ||
          state.startsWith('alarm-table') ||
          state === 'base-schematic';
        screen = schematic ? 'equipment-schematic' : 'equipment-model';
        const data = await mockEquipment(
          page,
          state === 'schematic-stale'
            ? 'stale'
            : state === 'schematic-unknown' ||
                state === 'schematic-polling-error' ||
                state === 'schematic-polling-disabled'
              ? 'unknown'
              : 'running'
        );
        if (state.startsWith('alarm-table'))
          data.alarms.push({
            ...data.alarms[0],
            pointer: '/dex/NOT_RAISED',
            anomaly: null,
            anomaly_status: null,
            label: 'Synthetic not-raised finding'
          });
        if (state === 'schematic-polling-error') {
          data.last_error_code = 'NETWORK';
          data.last_error_at = '2026-10-20T07:59:00Z';
          data.last_success_at = '2026-10-20T07:58:00Z';
        }
        if (state === 'schematic-polling-disabled') {
          data.enabled = false;
          for (const point of Object.values(data.points)) {
            point.value = null;
            point.reason = 'disabled';
          }
        }
        if (state === 'schematic-query-error')
          await page.route('**/api/machine-health/station/17/mimic/**', (r) =>
            r.fulfill({
              status: 503,
              json: { detail: 'Synthetic unavailable source' }
            })
          );
        if (state === 'schematic-shifted-history') {
          data.recorded_telemetry_as_live = false;
          data.display_shifted = true;
          data.display_shift_seconds = 400 * 86400;
        }
        if (state === '3d-no-slot' || state === '3d-unbound') {
          const positions = positionsFixture();
          const selected = positions.positions.find((p) => p.id === 'bearing')!;
          if (state === '3d-no-slot') {
            selected.components = [];
            selected.signals = [];
            selected.work = null;
          } else {
            selected.family = null;
            selected.components = [];
            selected.signals = [];
            selected.work = null;
          }
          await mockPositions(page, { data: positions });
        }
        await openEquipment(page, schematic, scheme, {
          role:
            state === 'alarm-table-acknowledger' ? 'acknowledger' : 'viewer',
          state: state === 'schematic-query-error' ? 'query-error' : ''
        });
        if (state === '3d-cutaway-hidden-markers')
          await page.getByText('Cutaway', { exact: true }).click();
        if (
          state === '3d-selected-bearing' ||
          state === '3d-no-slot' ||
          state === '3d-unbound'
        )
          await page.locator('[data-position="bearing"]').click();
        if (state === 'schematic-query-error')
          await expect(page.getByRole('alert').first()).toContainText(
            'Readings unavailable'
          );
        else if (schematic) {
          await expect(
            page.getByRole('group', {
              name: 'Schematic state legend',
              exact: true
            })
          ).toBeVisible();
          await expect(page.getByLabel('Process data')).toHaveCount(0);
          await expect(
            page.locator('[data-unit-drawing] animateMotion')
          ).toHaveCount(0);
          if (state === 'schematic-polling-error')
            await expect(page.getByRole('alert').first()).toContainText(
              'code NETWORK'
            );
          if (state === 'schematic-stale')
            await expect(page.getByText('7.5 kW', { exact: true })).toHaveCount(
              0
            );
          if (state === 'schematic-shifted-history')
            await expect(
              page.getByText('Recorded history shown as current', {
                exact: true
              })
            ).toBeVisible();
          await expect(
            page.locator('[data-alarm-status="acknowledged"]')
          ).toHaveAttribute('data-variant', 'outline');
          await expect(page.locator('tr[data-anomaly="904"]')).toContainText(
            'Alarm state unavailable'
          );
        }
      }
      for (const width of [1100, 1440, 1920, 390]) {
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
        await captureFromTop(page);
        if (state === 'schematic-shifted-history' && width === 390) {
          const history = page.getByRole('button', {
            name: 'Recorded history shown as current',
            exact: true
          });
          await page.mouse.move(1, 1);
          await history.focus();
          await expect(history).toBeFocused();
          const reason = page.getByRole('tooltip').filter({
            hasText:
              /^Times moved 400 d onto the display clock; plant times are earlier$/
          });
          await expect(reason).toBeVisible();
          await expect(reason).toHaveCSS('opacity', '1');
          const bounds = (await reason.boundingBox())!;
          await page.screenshot({
            path: info.outputPath(`history-tooltip-${scheme}.png`)
          });
          await writeFile(
            info.outputPath(`history-tooltip-${scheme}.json`),
            JSON.stringify(
              {
                ...bounds,
                viewportWidth: width,
                documentWidth: await page.evaluate(
                  () => document.documentElement.scrollWidth
                )
              },
              null,
              2
            )
          );
          expect(bounds.x).toBeGreaterThanOrEqual(0);
          expect(
            bounds.x + bounds.width,
            'The focused history reason must fit the phone viewport'
          ).toBeLessThanOrEqual(width);
          await page.keyboard.press('Escape');
          await expect(reason).toBeHidden();
          await history.blur();
        }
        const { metadata, stem } = await captureEvidence(
          page,
          info,
          screen,
          state,
          scheme,
          width
        );
        if (state.startsWith('alarm-table')) {
          const rows = page.locator('tr[data-point]');
          for (let i = 0; i < (await rows.count()); i++) {
            await writeFile(
              `${stem}-alarm-${i}.json`,
              JSON.stringify(
                {
                  ...metadata,
                  element: `tr[data-point] index ${i}`,
                  text: await rows.nth(i).innerText()
                },
                null,
                2
              )
            );
            await rows.nth(i).screenshot({
              path: `${stem}-alarm-${i}.png`,
              animations: 'disabled'
            });
          }
        }
        expect(
          metadata.contrastFailures,
          'All panel text has 4.5:1 contrast'
        ).toEqual([]);
        expect(
          metadata.nonTextFailures,
          'Control edges have 3:1 contrast'
        ).toEqual([]);
        expect(metadata.page.scrollWidth).toBeLessThanOrEqual(width);
        if (screen === 'equipment-schematic') {
          const information = page.getByRole('button', {
            name: 'Schematic information',
            exact: true
          });
          if (state === 'schematic-query-error') {
            await expect(information).toHaveCount(0);
          } else {
            await expect(information).toBeVisible();
            await page.keyboard.press('Tab');
            await information.focus();
            await expect(information).toBeFocused();
            const provenance = page
              .getByRole('tooltip')
              .filter({ hasText: 'Source: Synthetic SCADA' });
            await expect(provenance).toContainText('Source: Synthetic SCADA');
            await expect(provenance).toContainText(
              'Last poll attempt: 20 Oct 2026 11:00:00'
            );
            await expect(provenance).toContainText('your clock (Asia/Riyadh');
          }
        } else if (screen === 'machines') {
          const details = page.getByRole('button', {
            name: 'Location details',
            exact: true
          });
          await details.click();
          // No "Times:" caption; the clock is in the tooltip of the time shown.
          await expect(
            page.getByText(/Times: your clock|Times: plant clock/)
          ).toHaveCount(0);
          await page
            .getByText(/^Records read/)
            .locator('time')
            .hover();
          await expect(
            page
              .getByRole('tooltip')
              .filter({ hasText: /your clock|plant clock/ })
          ).toBeVisible();
          await details.click();
        } else expect(metadata.clockStatements).toBe(0);
        expect(
          metadata.drawing.tags.filter(
            (tag) => tag.valueOverlapsLabel || tag.labelOutsideTag
          ),
          'Reading labels and values remain separate and inside their boxes'
        ).toEqual([]);
        expect(
          metadata.drawing.partLabels.filter(
            (label) =>
              label.outside || label.hardTruncated || label.lineClamp !== 'none'
          )
        ).toEqual([]);
        expect(metadata.drawing.tagOverlaps).toEqual([]);
        expect(metadata.drawing.markerOverlaps).toEqual([]);
        expect(metadata.drawing.calloutOverlaps).toEqual([]);
        expect(metadata.drawing.drawingOverlaps).toEqual([]);
        if (metadata.drawing.tags.length)
          expect(metadata.drawing.partLabels).toHaveLength(13);
        if (state === 'base-machines') {
          if (width === 1440)
            expect(metadata.fold.pageFacts?.height).toBeLessThanOrEqual(40);
          if (width === 1440)
            expect(metadata.fold.machineRows).toBeGreaterThanOrEqual(5);
          if (width === 1920)
            expect(metadata.fold.machineRows).toBeGreaterThanOrEqual(8);
          if (width === 390) {
            const fold = metadata.fold.firstMachine!;
            await writeFile(
              info.outputPath(`machine-fold-${scheme}.json`),
              JSON.stringify(fold, null, 2)
            );
            expect(fold.measured).toBeGreaterThan(0);
            // Compare the painted border in physical pixels; text and controls
            // must still fit without any rounding allowance.
            expect(
              Math.round(fold.bottom * fold.pixelRatio)
            ).toBeLessThanOrEqual(
              Math.round(fold.viewportHeight * fold.pixelRatio)
            );
            expect(fold.contentBottom).toBeLessThanOrEqual(fold.viewportHeight);
          }
        }
        if (state === 'base-fault-history' && width === 1440)
          expect(metadata.fold.occurrenceRows).toBeGreaterThanOrEqual(3);
        if (state === 'alarm-resolved' && width === 1440) {
          expect(metadata.fold.openedState?.bottom).toBeLessThanOrEqual(900);
          expect(metadata.fold.occurrenceHeader?.bottom).toBeLessThanOrEqual(
            900
          );
        }
        if (state === 'base-schematic') {
          const readings = page.locator('[data-unit-schematic] [data-reading]');
          await expect(readings).toHaveCount(4);
          await expect(readings.locator('[data-value] button')).toHaveCount(0);
          for (const reading of await readings.all()) {
            const label = (
              await reading.locator('[data-tag-label]').innerText()
            ).trim();
            const target = reading.locator('[data-value] > span');
            const value = await target.evaluate((node) =>
              [...node.childNodes]
                .filter((child) => child.nodeType === Node.TEXT_NODE)
                .map((child) => child.textContent)
                .join('')
                .trim()
            );
            expect(value).not.toBe('');
            await expect(target).toContainText(`${label}: ${value}`);
            await expect(target).not.toHaveAttribute('tabindex');
            await target.hover();
            const tooltip = page
              .getByRole('tooltip')
              .filter({ hasText: `${label}: ${value}` });
            await expect(tooltip).toBeVisible();
            await expect(tooltip).toContainText('20 Oct 2026 11:00:00');
            await expect(tooltip).toContainText('Asia/Riyadh');
          }
          expect(metadata.drawing.tags).toHaveLength(4);
          expect(metadata.drawing.tagOverlaps).toEqual([]);
          expect(metadata.drawing.markerOverlaps).toEqual([]);
          expect(metadata.drawing.calloutOverlaps).toEqual([]);
          expect(metadata.drawing.drawingOverlaps).toEqual([]);
          if (metadata.drawing.tags.length)
            expect(metadata.drawing.partLabels).toHaveLength(13);
          if (width === 1100 || width === 1440)
            for (const tag of metadata.drawing.tags)
              expect(tag.labelSize).toBeGreaterThanOrEqual(
                width === 1440 ? 11.95 : 10.95
              );
          if (width !== 390) {
            // The owner prioritizes a dominant drawing under the native
            // chrome; the complete schematic remains available by scrolling.
            const drawing = page.locator('[data-unit-drawing]');
            const bounds = (await drawing.boundingBox())!;
            const content = (await page
              .locator('[data-unit-schematic]')
              .boundingBox())!;
            const painted = await drawing
              .locator('g > svg')
              .evaluate((node) => {
                const svg = node as SVGSVGElement;
                const box = svg.getBBox();
                const transform = svg.getScreenCTM();
                if (!transform)
                  throw new Error('Schematic has no screen transform');
                const corners = [
                  new DOMPoint(box.x, box.y),
                  new DOMPoint(box.x + box.width, box.y),
                  new DOMPoint(box.x, box.y + box.height),
                  new DOMPoint(box.x + box.width, box.y + box.height)
                ].map((point) => point.matrixTransform(transform));
                const frame = svg.closest<SVGSVGElement>(
                  '[data-unit-drawing]'
                )!;
                const left = Math.min(...corners.map((point) => point.x));
                const right = Math.max(...corners.map((point) => point.x));
                const top = Math.min(...corners.map((point) => point.y));
                const bottom = Math.max(...corners.map((point) => point.y));
                return {
                  width: right - left,
                  height: bottom - top,
                  left,
                  right,
                  top,
                  bottom,
                  nativeAspect:
                    frame.viewBox.baseVal.width / frame.viewBox.baseVal.height
                };
              });
            expect(bounds.width).toBeGreaterThanOrEqual(content.width * 0.5);
            expect(painted.width).toBeGreaterThanOrEqual(content.width * 0.5);
            // Both container layouts keep the cropped native aspect.
            expect(
              Math.abs(bounds.height - bounds.width / painted.nativeAspect)
            ).toBeLessThanOrEqual(1);
            expect(painted.left).toBeGreaterThanOrEqual(bounds.x - 1);
            expect(painted.right).toBeLessThanOrEqual(
              bounds.x + bounds.width + 1
            );
            expect(painted.top).toBeGreaterThanOrEqual(bounds.y - 1);
            expect(painted.bottom).toBeLessThanOrEqual(
              bounds.y + bounds.height + 1
            );
            const hostWidth = await drawing.evaluate((node) => {
              let host: HTMLElement | null = node as HTMLElement;
              while (
                host &&
                getComputedStyle(host).containerType !== 'inline-size'
              )
                host = host.parentElement;
              return host?.getBoundingClientRect().width ?? 0;
            });
            expect(hostWidth).toBeGreaterThan(0);
            for (const callout of await page
              .locator('[data-unit-callouts] button[data-part]')
              .all()) {
              const box = (await callout.boundingBox())!;
              if (hostWidth >= 1100)
                expect(
                  box.x + box.width <= bounds.x + 1 ||
                    box.x >= bounds.x + bounds.width - 1,
                  'Wide-container callouts stay beside the drawing'
                ).toBe(true);
              else
                expect(
                  box.y,
                  'Narrow-container callouts stay below the drawing'
                ).toBeGreaterThanOrEqual(bounds.y + bounds.height - 1);
            }
          }
          if (width === 390) {
            const key = (await page
              .getByLabel('Key readings', { exact: true })
              .boundingBox())!;
            expect(key.y + key.height).toBeLessThanOrEqual(844);
          }
        }
        if (state === 'base-model' || state === '3d-cutaway-hidden-markers') {
          if (width === 1100)
            expect(metadata.fold.canvas?.width).toBeGreaterThanOrEqual(560);
          if (width === 1440)
            expect(metadata.fold.components?.y).toBeLessThan(900);
        }
      }
    });
