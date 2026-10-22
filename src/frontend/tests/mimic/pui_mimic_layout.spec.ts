import { readFileSync } from 'node:fs';
import { type Locator, type Page, expect, test } from '@playwright/test';
import type {
  MimicData,
  MimicLayout,
  MimicPoint
} from '../../src/pages/assets/health/mimic/types';

// Synthetic readings exercise the checked-in engineering mapping, not a copy
// of the old crowded screen's coordinates or any plant/customer data.
const layout = JSON.parse(
  readFileSync(
    new URL(
      '../../../backend/InvenTree/machine_health/layouts/pumphouse.layout.json',
      import.meta.url
    ),
    'utf8'
  )
) as MimicLayout;
const observed = '2026-10-22T06:32:41Z';
const bearingName = 'Motor non-drive-end bearing vibration and temperature';
const sizes = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'tablet', width: 1024, height: 768 },
  { name: 'phone', width: 390, height: 844 }
] as const;

function point(
  pointer: string,
  label: string,
  group: string,
  value: MimicPoint['value'],
  unit = '',
  extra: Partial<MimicPoint> = {}
): MimicPoint {
  return {
    pointer,
    label,
    group,
    value,
    unit,
    quality: 'good',
    observed_at: observed,
    age_seconds: 0,
    unchanged_for_seconds: null,
    reason: value === null ? 'not_published' : null,
    condition: 'unknown',
    thresholds_configured: false,
    ...extra
  };
}

function pointerFor(template: string, unit: string) {
  return template
    .replaceAll('{pump_number}', unit.slice(1))
    .replaceAll('{pump}', unit);
}

function unitPoints(unit: string): Record<string, MimicPoint> {
  const points = Object.fromEntries(
    layout.elements
      .filter((element) => element.view === 'unit')
      .map((element) => {
        const pointer = pointerFor(element.pointer, unit);
        const value =
          element.id === 'pump-pressure'
            ? null
            : element.role === 'status'
              ? 'I'
              : element.id === 'pump-speed'
                ? 0.42
                : 0;
        const engineeringUnit =
          element.id === 'pump-power'
            ? 'MW'
            : element.id === 'pump-speed'
              ? 'rpm'
              : element.id === 'pump-flow'
                ? 'cusec'
                : '';
        return [
          pointer,
          point(
            pointer,
            element.label,
            'Process readings',
            value,
            engineeringUnit
          )
        ];
      })
  );
  for (const [index, part] of (layout.parts ?? []).entries()) {
    // Empty guide-bearing coverage exercises an absent part without implying
    // a fault or fabricating the missing pressure measurement.
    if (part.code === 'PS-GUIDE-BRG') continue;
    const group = part.code === 'PS-MOTOR-NDE-BRG' ? bearingName : part.label;
    const vibration = ['PS-MOTOR-NDE-BRG', 'PS-MOTOR-DE-BRG'].includes(
      part.code
    );
    const valve = ['PS-EOPD-VALVE', 'PS-HOPD-VALVE'].includes(part.code);
    const flow = part.code === 'PS-DISCHARGE';
    for (
      let sensor = 1;
      sensor <= (vibration ? 2 : valve || flow ? 1 : 3);
      sensor++
    ) {
      const pointer = `/synthetic/${unit}/${part.code}/${sensor}`;
      points[pointer] = point(
        pointer,
        `${part.label} ${sensor}`,
        group,
        vibration
          ? 0.1 + sensor * 0.02
          : valve
            ? 99
            : flow
              ? 0
              : 39 + index * 0.3 + sensor * 0.2,
        vibration ? 'mm/s' : valve ? '%' : flow ? 'cusec' : 'degC',
        { part_code: part.code }
      );
    }
  }
  return points;
}

function fixture(unit: string | null): MimicData {
  return {
    station: 17,
    name: 'Synthetic fourteen-bay station',
    generated_at: observed,
    plant_timezone: 'Asia/Riyadh',
    recorded_telemetry_as_live: true,
    last_observed_at: observed,
    reporting: true,
    enabled: true,
    polling_enabled: true,
    source: { pk: 1, name: 'Synthetic regression fixture' },
    last_poll_at: observed,
    last_error_code: '',
    layout,
    station_points: Object.fromEntries(
      layout.elements
        .filter((element) => element.view === 'station')
        .map((element) => [
          element.pointer,
          point(
            element.pointer,
            element.label,
            'Station readings',
            element.id === 'forebay'
              ? 120.972
              : element.role === 'status'
                ? 'I'
                : 0,
            element.id === 'forebay' ? 'm' : ''
          )
        ])
    ),
    bays: Array.from({ length: 14 }, (_, index) => {
      const key = `P${index + 1}`;
      const points = unitPoints(key);
      const power = layout.elements.find(
        (element) => element.id === 'pump-power'
      );
      if (key === 'P3' && power) {
        const pointer = pointerFor(power.pointer, key);
        points[pointer] = {
          ...points[pointer],
          value: null,
          reason: 'not_published'
        };
      }
      return {
        key,
        machine: index + 1,
        name: `Synthetic pump ${index + 1}`,
        active: true,
        state: 'idle',
        alarm: null,
        points
      };
    }),
    selected_unit: unit,
    points: unit ? unitPoints(unit) : {},
    totals: {
      power: {
        value: null,
        unit: 'MW',
        derived: true,
        reason: 'incomplete',
        contributors: []
      },
      flow: {
        value: 0,
        unit: 'm³/s',
        derived: true,
        reason: null,
        contributors: []
      }
    },
    alarms: [],
    unconfigured_thresholds: 686
  };
}

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
  const targets = await page.locator('button, a, input').evaluateAll((nodes) =>
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

      test('station readings and fourteen selectable pumps stay readable and separate', async ({
        page
      }) => {
        await page.goto(`/?scheme=${scheme}`);
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
        ).toHaveText('120.972 m');
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
        expect(
          Math.abs(
            (await box(totals.nth(0))).height -
              (await box(totals.nth(1))).height
          )
        ).toBeLessThanOrEqual(1);
        expect(
          Math.abs(
            (await box(page.getByText('Plant power', { exact: true }))).y -
              (await box(page.getByText('Plant flow', { exact: true }))).y
          )
        ).toBeLessThanOrEqual(1);
        await expect(
          page.locator('[data-total="power"] button').last()
        ).toHaveText('—');
        await expect(page.locator('[data-total="flow"]')).toContainText(
          '0 m³/s'
        );
        await noCaptions(page);
        await expect(
          page.locator('[data-section="station"] button')
        ).toHaveText('Station readings');
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
          page.getByRole('heading', { name: 'Pump unit: P14', exact: true })
        ).toBeVisible();
        await page
          .getByRole('button', { name: 'Overview only', exact: true })
          .click();
        await expect(
          page.getByRole('heading', { name: 'Pump unit: P14', exact: true })
        ).toHaveCount(0);
        await expect(page.locator('[data-section="station"]')).toBeVisible();

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
          page.getByRole('tooltip').filter({ hasText: 'awaits plant review' })
        ).toBeVisible();
      });

      test('P7 values, full part labels and native markers stay clear of the pump', async ({
        page
      }) => {
        await page.goto(`/?scheme=${scheme}&pump=P7`);
        const drawing = page.locator('[data-unit-drawing]');
        await expect(drawing).toBeVisible();
        const callouts = page.locator(
          '[data-callout-column] button[data-part]'
        );
        await expect(callouts).toHaveCount(13);
        const readings = page.locator('[data-unit-schematic] [data-reading]');
        await disjoint([
          drawing,
          ...(await readings.all()),
          ...(await callouts.all())
        ]);
        for (const name of await callouts.locator('[data-part-label]').all()) {
          const height = await name.evaluate((node) => ({
            actual: node.getBoundingClientRect().height,
            line: Number.parseFloat(getComputedStyle(node).lineHeight)
          }));
          expect(height.actual).toBeLessThanOrEqual(height.line * 2 + 1);
        }
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
            layout.parts ?? []
          );
        expect(markers).toHaveLength(13);
        for (const [index, marker] of markers.entries()) {
          expect(marker.number).toBe(String(index + 1));
          expect(
            marker.xError,
            `${marker.code}: native position x`
          ).toBeLessThanOrEqual(1);
          expect(
            marker.yError,
            `${marker.code}: native position y`
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
        for (const label of ['Flow direction', 'Communication status']) {
          const missing = page.getByRole('button', {
            name: `${label}: Not reported by the plant`,
            exact: true
          });
          await expect(missing).toHaveText('—');
          await focusTooltip(page, missing);
          await expect(
            page
              .getByRole('tooltip')
              .filter({ hasText: `${label}: Not reported by the plant` })
          ).toBeVisible();
        }
        await page.getByLabel('Filter unit readings', { exact: true }).focus();
        await noCaptions(page);
        // The callouts already carry the sensor summaries. Closed reading
        // groups keep just their identity and the shared reference number.
        for (const section of await page.locator('[data-section]').all()) {
          const title = (await section.getAttribute('data-section')) ?? '';
          const escaped = title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          await expect(
            section.getByRole('button', { name: title, exact: true })
          ).toHaveText(new RegExp(`^\\s*(?:\\d+\\s*)?${escaped}\\s*$`));
        }
        await contained(page, size.name === 'phone');
        const bearing = callouts.filter({
          has: page.locator('[data-part-label]', { hasText: bearingName })
        });
        await focusTooltip(page, bearing);
        await expect(
          page.getByRole('tooltip').filter({ hasText: bearingName })
        ).toBeVisible();
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
