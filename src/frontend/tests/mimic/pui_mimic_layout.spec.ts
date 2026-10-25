import { type Locator, type Page, expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  bearingName,
  fixture,
  layout,
  markerCoordinates,
  observed
} from './layoutFixtures';
import { mimicEntry } from './navigation';

nativeBaseline();

const sizes = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'tablet', width: 1024, height: 768 },
  { name: 'phone', width: 390, height: 844 }
] as const;

type Rectangle = { x: number; y: number; width: number; height: number };

async function box(locator: Locator): Promise<Rectangle> {
  const rectangle = await locator.boundingBox();
  expect(
    rectangle,
    'The measured operational element is rendered'
  ).not.toBeNull();
  return rectangle!;
}

function overlaps(first: Rectangle, second: Rectangle) {
  return (
    first.x < second.x + second.width - 0.5 &&
    first.x + first.width > second.x + 0.5 &&
    first.y < second.y + second.height - 0.5 &&
    first.y + first.height > second.y + 0.5
  );
}

async function disjoint(locators: Locator[]) {
  const rectangles = await Promise.all(locators.map(box));
  for (let first = 0; first < rectangles.length; first++) {
    for (let second = first + 1; second < rectangles.length; second++) {
      expect(
        overlaps(rectangles[first], rectangles[second]),
        `Operational elements ${first + 1} and ${second + 1} overlap`
      ).toBe(false);
    }
  }
}

async function contained(page: Page, phone: boolean) {
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth)
  ).toBeLessThanOrEqual((page.viewportSize()?.width ?? 0) + 1);
  if (!phone) return;
  // Measure the production mimic controls, excluding the native harness's
  // separate fixture-refresh toolbar.
  const controls = page.locator(
    '[data-mimic-motion] button, [data-mimic-motion] a, [data-mimic-motion] input'
  );
  expect(await controls.count()).toBeGreaterThan(0);
  const targets = await controls.evaluateAll((nodes) =>
    nodes
      .map((node) => {
        const rectangle = node.getBoundingClientRect();
        return {
          name:
            node.getAttribute('aria-label') ?? node.textContent ?? node.tagName,
          width: rectangle.width,
          height: rectangle.height
        };
      })
      .filter((rectangle) => rectangle.width > 0 && rectangle.height > 0)
  );
  for (const target of targets) {
    expect(
      target.width,
      `${target.name}: phone target width`
    ).toBeGreaterThanOrEqual(44);
    expect(
      target.height,
      `${target.name}: phone target height`
    ).toBeGreaterThanOrEqual(44);
  }
}

async function noCaptions(page: Page) {
  for (const caption of [
    'This schematic awaits plant review. Missing mappings are shown as unavailable.',
    'Select a pump bay to view its instruments and readings.',
    'Drawing scrolls sideways',
    'A numbered part is listed below under the same number. A system without a number has no place on the drawing.',
    'Pipes and valves drawn neutral: flow direction and valve position are not supplied.',
    'Open a reading group for values, units, quality and observation times.'
  ]) {
    await expect(page.getByText(caption, { exact: true })).toBeHidden();
  }
  await expect(
    page.getByText(/^Points without configured thresholds/)
  ).toBeHidden();
  await expect(page.getByText(/^\d+ unavailable$/)).toBeHidden();
  await expect(
    page.getByText('Times: your clock', { exact: true })
  ).toHaveCount(0);
}

async function focusTooltip(page: Page, target: Locator) {
  // Mantine exposes focus tooltips only for keyboard-visible focus. Enter
  // keyboard modality explicitly after any preceding pointer interaction.
  await page.keyboard.press('Tab');
  await target.focus();
  await expect(target).toBeFocused();
}

for (const size of sizes) {
  for (const scheme of ['light', 'dark'] as const) {
    test.describe(`${size.name} ${scheme} mimic layout`, () => {
      test.beforeEach(async ({ page }) => {
        await page.setViewportSize({ width: size.width, height: size.height });
        await page.clock.install({ time: new Date(observed) });
        await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
          route.fulfill({
            json: fixture(
              new URL(route.request().url()).searchParams.get('unit')
            )
          })
        );
      });

      test('running count exposes a conflicting source counter without inventing a running pump', async ({
        page
      }, info) => {
        await page.route(
          '**/api/machine-health/station/17/mimic/**',
          (route) => {
            const data = fixture(null);
            data.station_points['/pc'].value = 1;
            data.station_points['/st'].value = 'R';
            return route.fulfill({ json: data });
          }
        );
        await page.goto(mimicEntry(`/?scheme=${scheme}`));
        await expect(
          page.locator(
            '[data-station-metric="pumps-running"] [data-reading-value]'
          )
        ).toHaveText('0 of 14');
        await expect(page.locator('[data-count-mismatch]')).toHaveText(
          'Count mismatch — station counter reports 1. Check source status readings.'
        );
        await expect(
          page.locator('g[data-operating-state="running"]')
        ).toHaveCount(0);
        await expect(
          page.locator('g[data-operating-state="idle"]')
        ).toHaveCount(14);
        await contained(page, size.name === 'phone');
        await page.screenshot({
          path: info.outputPath(
            `station-count-mismatch-${size.name}-${scheme}.png`
          ),
          fullPage: true,
          animations: 'disabled'
        });
      });

      test('running count and the identified running pump use the same state', async ({
        page
      }, info) => {
        await page.route(
          '**/api/machine-health/station/17/mimic/**',
          (route) => {
            const data = fixture(null);
            data.station_points['/pc'].value = 1;
            const runningBay = data.bays.find((bay) => bay.key === 'P5')!;
            runningBay.state = 'running';
            runningBay.points['/pd/P5/st'].value = 'R';
            return route.fulfill({ json: data });
          }
        );
        await page.goto(mimicEntry(`/?scheme=${scheme}`));
        await expect(
          page.locator(
            '[data-station-metric="pumps-running"] [data-reading-value]'
          )
        ).toHaveText('1 of 14');
        await expect(
          page.locator('g[data-operating-state="running"]')
        ).toHaveCount(1);
        await expect(page.locator('g[data-bay="P5"]')).toHaveAttribute(
          'data-operating-state',
          'running'
        );
        await expect(
          page.locator('g[data-bay="P5"] [data-running-icon]')
        ).toHaveCount(1);
        await expect(page.locator('[data-count-mismatch]')).toHaveCount(0);
        await page.screenshot({
          path: info.outputPath(
            `station-count-running-${size.name}-${scheme}.png`
          ),
          fullPage: true,
          animations: 'disabled'
        });
      });

      test('station readings and fourteen selectable pumps stay readable and separate', async ({
        page
      }) => {
        await page.goto(mimicEntry(`/?scheme=${scheme}`));
        const metrics = page.locator('[data-station-metric]');
        await expect(metrics).toHaveCount(3);
        const bays = page.locator('g[data-bay]');
        await expect(bays).toHaveCount(14);
        await disjoint(await metrics.all());
        for (const metric of await metrics.all()) {
          const card = await box(metric);
          const value = await metric
            .locator('[data-reading-value]')
            .evaluate((node) => {
              const range = document.createRange();
              range.selectNodeContents(node);
              const rectangle = range.getBoundingClientRect();
              return {
                x: rectangle.x,
                y: rectangle.y,
                width: rectangle.width,
                height: rectangle.height
              };
            });
          expect(value.x).toBeGreaterThanOrEqual(card.x);
          expect(value.y).toBeGreaterThanOrEqual(card.y);
          expect(value.x + value.width).toBeLessThanOrEqual(
            card.x + card.width + 1
          );
          expect(value.y + value.height).toBeLessThanOrEqual(
            card.y + card.height + 1
          );
          for (const bay of await bays.all())
            expect(overlaps(card, await box(bay))).toBe(false);
        }
        await expect(
          page.locator('[data-station-metric="forebay"] [data-reading-value]')
        ).toHaveText('120.97 m');
        await expect(
          page.locator(
            '[data-station-metric="pumps-running"] [data-reading-value]'
          )
        ).toHaveText('0 of 14');
        await expect(
          page.locator(
            '[data-station-metric="station-status"] [data-reading-value]'
          )
        ).toHaveText('Idle');
        // A missing power measurement keeps the supplied idle state, never 0 MW.
        await expect(page.locator('g[data-bay="P3"]')).toContainText('Idle');
        await expect(page.locator('g[data-bay="P3"]')).not.toContainText(
          '0 MW'
        );

        const totals = page.locator('[data-total]');
        await expect(totals).toHaveCount(2);
        await disjoint(await totals.all());
        const strip = page.locator('[data-station-metrics]');
        const stripMetrics = strip.locator(
          '[data-station-metric], [data-total]'
        );
        await expect(stripMetrics).toHaveCount(5);
        await disjoint(await stripMetrics.all());
        expect(
          Math.abs(
            (await box(totals.nth(0))).height -
              (await box(totals.nth(1))).height
          )
        ).toBeLessThanOrEqual(1);
        if (size.name !== 'phone') {
          const rows = await stripMetrics.evaluateAll((nodes) =>
            nodes.map((node) => node.getBoundingClientRect().top)
          );
          expect(Math.max(...rows) - Math.min(...rows)).toBeLessThanOrEqual(1);
        } else {
          await expect(
            page.getByText('Swipe to view all pump bays', { exact: true })
          ).toHaveCount(0);
          const cardsPerRow = await stripMetrics.evaluateAll((nodes) => {
            const rows = new Map<number, number>();
            for (const node of nodes) {
              const top = Math.round(node.getBoundingClientRect().top);
              rows.set(top, (rows.get(top) ?? 0) + 1);
            }
            return [...rows.values()];
          });
          expect(cardsPerRow).toEqual([2, 3]);
        }
        await expect(
          page.locator('[data-total="power"] button').last()
        ).toHaveText('—');
        await expect(page.locator('[data-total="flow"]')).toContainText(
          '0 m³/s'
        );
        await noCaptions(page);
        await expect(page.locator('[data-section="station"]')).toHaveCount(0);
        await contained(page, size.name === 'phone');

        await page
          .getByRole('button', { name: 'P1: Idle', exact: true })
          .focus();
        for (let index = 1; index < 14; index++)
          await page.keyboard.press('Tab');
        const last = page.getByRole('button', {
          name: 'P14: Idle',
          exact: true
        });
        await expect(last).toBeFocused();
        const viewport = await box(
          page.getByRole('region', { name: 'Station pump bays' })
        );
        const lastBay = await box(last);
        expect(lastBay.x).toBeGreaterThanOrEqual(viewport.x - 1);
        expect(lastBay.x + lastBay.width).toBeLessThanOrEqual(
          viewport.x + viewport.width + 1
        );
        await page.keyboard.press('Enter');
        await expect(
          page.getByRole('heading', { name: 'Synthetic pump 14', exact: true })
        ).toBeVisible();
        await page
          .getByRole('button', { name: 'Overview only', exact: true })
          .click();
        await expect(
          page.getByRole('heading', { name: 'Synthetic pump 14', exact: true })
        ).toHaveCount(0);
        await expect(stripMetrics).toHaveCount(5);
        await expect(page.locator('[data-section="station"]')).toHaveCount(0);

        await focusTooltip(
          page,
          page.getByRole('button', {
            name: 'Plant power: Missing contributors',
            exact: true
          })
        );
        await expect(
          page.getByRole('tooltip').filter({ hasText: 'Missing contributors' })
        ).toBeVisible();
        await focusTooltip(
          page,
          page.getByRole('button', { name: 'Schematic awaiting plant review' })
        );
        await expect(
          page
            .getByRole('tooltip')
            .filter({ hasText: 'Layout pending plant approval' })
        ).toContainText('Synthetic regression fixture');
      });

      test('P7 values, full part labels and stand-off markers stay clear of the pump', async ({
        page
      }) => {
        await page.goto(mimicEntry(`/?scheme=${scheme}&pump=P7`));
        const drawing = page.locator('[data-unit-drawing]');
        await expect(drawing).toBeVisible();
        const callouts = page.locator(
          '[data-callout-column] button[data-part]'
        );
        await expect(callouts).toHaveCount(13);
        for (const text of await callouts.allTextContents()) {
          expect(text).not.toMatch(/degC|percent|dv:|-0(?:\s|$)/);
        }
        await expect(callouts.filter({ hasText: 'EOPD valve' })).toContainText(
          '99 %'
        );
        const readings = page.locator('[data-unit-schematic] [data-reading]');
        await disjoint([
          drawing,
          ...(await readings.all()),
          ...(await callouts.all())
        ]);
        const drawingBox = await box(drawing);
        const contentBox = await box(page.locator('[data-unit-schematic]'));
        const sideColumns = contentBox.width >= 1100;
        expect(drawingBox.width).toBeGreaterThanOrEqual(contentBox.width * 0.5);
        if (size.name === 'desktop') {
          expect(drawingBox.height).toBeGreaterThanOrEqual(size.height * 0.6);
          // Verify the painted native geometry, not only a wide SVG box.
          const geometry = await drawing.locator('g > svg').evaluate((node) => {
            const svg = node as SVGSVGElement;
            const bounds = svg.getBBox();
            const transform = svg.getScreenCTM();
            const scale = transform ? Math.hypot(transform.a, transform.b) : 0;
            return {
              width: bounds.width * scale,
              height: bounds.height * scale
            };
          });
          expect(geometry.width).toBeGreaterThanOrEqual(contentBox.width * 0.5);
          expect(geometry.height).toBeGreaterThanOrEqual(size.height * 0.6);
        }
        for (const side of ['left', 'right'] as const) {
          for (const callout of await page
            .locator(`[data-callout-column="${side}"] button[data-part]`)
            .all()) {
            const calloutBox = await box(callout);
            if (!sideColumns) {
              expect(calloutBox.y).toBeGreaterThanOrEqual(
                drawingBox.y + drawingBox.height - 1
              );
            } else if (side === 'left') {
              expect(calloutBox.x + calloutBox.width).toBeLessThanOrEqual(
                drawingBox.x + 1
              );
            } else {
              expect(calloutBox.x).toBeGreaterThanOrEqual(
                drawingBox.x + drawingBox.width - 1
              );
            }
          }
        }
        for (const name of await callouts.locator('[data-part-label]').all()) {
          const bounds = await name.evaluate((node) => {
            const range = document.createRange();
            range.selectNodeContents(node);
            const text = range.getBoundingClientRect();
            const label = node.getBoundingClientRect();
            return {
              textHeight: text.height,
              height: label.height,
              textWidth: text.width,
              width: label.width,
              lineClamp: getComputedStyle(node).webkitLineClamp
            };
          });
          expect(
            bounds.lineClamp,
            'The complete part name remains visible'
          ).toBe('none');
          expect(bounds.textHeight).toBeLessThanOrEqual(bounds.height + 1);
          expect(bounds.textWidth).toBeLessThanOrEqual(bounds.width + 1);
        }
        if (size.name === 'phone') {
          const left = await page
            .locator('[data-callout-column="left"] button[data-part]')
            .all();
          const right = await page
            .locator('[data-callout-column="right"] button[data-part]')
            .all();
          for (let row = 0; row < Math.min(left.length, right.length); row++) {
            const a = await box(left[row]);
            const b = await box(right[row]);
            expect(
              Math.abs(a.y - b.y),
              'Spatial callouts share a row'
            ).toBeLessThanOrEqual(1);
            expect(
              Math.abs(a.height - b.height),
              'Full names do not stagger the next row'
            ).toBeLessThanOrEqual(1);
          }
        }
        const expectedMarkers = (layout.parts ?? []).map((part, index) => ({
          ...part,
          x: markerCoordinates[index][0],
          y: markerCoordinates[index][1]
        }));
        const markers = await page
          .locator('[data-part-markers] > [data-part]')
          .evaluateAll(
            (nodes, parts) =>
              nodes.map((node) => {
                const part = parts.find(
                  (item) => item.code === node.getAttribute('data-part')
                );
                const circle = node.querySelector('circle');
                const text = node.querySelector('text');
                const matrix = (
                  node as SVGElement
                ).ownerSVGElement?.getScreenCTM();
                if (!part || !circle || !text || !matrix)
                  throw new Error('Missing native marker geometry');
                const expected = new DOMPoint(part.x, part.y).matrixTransform(
                  matrix
                );
                const rectangle = circle.getBoundingClientRect();
                return {
                  code: part.code,
                  number: text.textContent,
                  xError: Math.abs(
                    rectangle.x + rectangle.width / 2 - expected.x
                  ),
                  yError: Math.abs(
                    rectangle.y + rectangle.height / 2 - expected.y
                  ),
                  font:
                    Number.parseFloat(getComputedStyle(text).fontSize) *
                    Math.hypot(matrix.a, matrix.b)
                };
              }),
            expectedMarkers
          );
        expect(markers).toHaveLength(13);
        for (const [index, marker] of markers.entries()) {
          expect(marker.number).toBe(String(index + 1));
          expect(
            marker.xError,
            `${marker.code}: reviewed annotation x`
          ).toBeLessThanOrEqual(1);
          expect(
            marker.yError,
            `${marker.code}: reviewed annotation y`
          ).toBeLessThanOrEqual(1);
          expect(
            marker.font,
            `${marker.code}: rendered number legibility`
          ).toBeGreaterThanOrEqual(13);
        }
        await expect(readings.filter({ hasText: 'Input power' })).toContainText(
          '0 MW'
        );
        await expect(readings.filter({ hasText: 'Shaft speed' })).toContainText(
          '0.42 rpm'
        );
        const pressure = readings
          .filter({ hasText: 'Discharge pressure' })
          .getByRole('button');
        await expect(pressure).toHaveText('—');
        await expect(pressure).toHaveAccessibleName(
          /Not published by this station/
        );
        await expect(
          page.getByLabel('Process data', { exact: true })
        ).toHaveCount(0);
        await expect(
          readings.filter({ hasText: 'Discharge flow' })
        ).toContainText('0 cusec');
        await expect(page.locator('[data-bar]')).toHaveCount(0);
        for (const side of ['left', 'right'] as const) {
          const codes = await page
            .locator(`[data-callout-column="${side}"] button[data-part]`)
            .evaluateAll((nodes) =>
              nodes.map((node) => node.getAttribute('data-part'))
            );
          const expected = expectedMarkers
            .filter((part) => (side === 'left' ? part.x < 550 : part.x >= 550))
            .sort((a, b) => a.y - b.y || a.x - b.x)
            .map((part) => part.code);
          expect(codes).toEqual(expected);
        }
        if (size.name === 'desktop') {
          const height = (await box(drawing)).height;
          await page.evaluate(() =>
            window.scrollTo(0, document.body.scrollHeight)
          );
          await page.setViewportSize({
            width: size.width + 1,
            height: size.height
          });
          await expect
            .poll(async () => Math.abs((await box(drawing)).height - height))
            .toBeLessThanOrEqual(2);
          await page.setViewportSize({
            width: size.width,
            height: size.height
          });
          await page.evaluate(() => window.scrollTo(0, 0));
        }
        await page.getByLabel('Filter unit readings', { exact: true }).focus();
        await noCaptions(page);
        // The callouts already carry the sensor summaries. Closed reading
        // groups keep just their identity and the shared reference number.
        for (const section of await page.locator('[data-section]').all()) {
          const title = (await section.getAttribute('data-section')) ?? '';
          const escaped = title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          await expect(section.locator('button').first()).toHaveAccessibleName(
            new RegExp(`^(?:\\d+\\s*)?${escaped}$`)
          );
          await expect(section.locator('button').first()).toHaveText(
            new RegExp(`^\\s*(?:\\d+\\s*)?${escaped}\\s*$`)
          );
        }
        await contained(page, size.name === 'phone');
        const bearing = callouts.filter({
          has: page.locator('[data-part-label]', { hasText: bearingName })
        });
        await focusTooltip(page, bearing);
        await expect(
          page.getByRole('tooltip').filter({ hasText: bearingName })
        ).toBeVisible();
        await bearing.click();
        const opened = page.locator(`[data-section="${bearingName}"]`);
        await expect(opened.locator('button').first()).toBeFocused();
        await expect(opened.locator('button').first()).toHaveAttribute(
          'aria-expanded',
          'true'
        );
        await expect(
          opened.locator('tbody tr td:nth-child(2) button')
        ).toHaveCount(0);
        await focusTooltip(page, pressure);
        await expect(
          page
            .getByRole('tooltip')
            .filter({ hasText: 'Not published by this station' })
        ).toBeVisible();
      });
    });
  }
}
