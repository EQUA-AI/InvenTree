import { expect, test } from '@playwright/test';
import { observed } from '../mimic/layoutFixtures';
import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { components } from '../s2/partsFixtures';
import { HEALTH_SIGNALS, openHealth, signalTable } from '../s3/healthFixtures';
import { nonText, readable } from './contrast';
import { captureFromTop } from './designFixtures';
import { ownerMimicFixture } from './ownerFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();

for (const scheme of ['light', 'dark'] as const) {
  for (const screen of ['station', 'pump'] as const) {
    test(`follow-up 2 ${screen} geometry, alarm identity and contrast ${scheme}`, async ({
      page
    }, info) => {
      if (!info.project.name.endsWith('phone'))
        await page.setViewportSize({ width: 1440, height: 900 });
      await page.clock.setFixedTime(new Date(observed));
      await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
        route.fulfill({
          json: ownerMimicFixture(
            new URL(route.request().url()).searchParams.get('unit')
          )
        })
      );
      await page.goto(
        `/tests/mimic/index.html?${new URLSearchParams({ scheme, ...(screen === 'pump' ? { pump: 'P7' } : {}) })}`
      );
      const phone = info.project.name.endsWith('phone');
      await expect(
        page
          .locator(
            screen === 'pump' ? '[data-unit-drawing]' : '[data-station-metric]'
          )
          .first()
      ).toBeVisible();
      await captureFromTop(page);
      const alarms = page.getByRole('table').filter({
        has: page.getByRole('columnheader', { name: 'Alarm', exact: true })
      });
      if (screen === 'station') {
        await expect(
          alarms.getByRole('columnheader', { name: 'Pump', exact: true })
        ).toHaveCount(1);
        const cooling = alarms
          .locator('tbody tr')
          .filter({ hasText: 'Pump cooling water 1' });
        await expect(cooling).toHaveCount(2);
        await expect(
          cooling.filter({ hasText: '82 °C' }).locator('[data-label="Pump"]')
        ).toHaveText('P1');
        await expect(
          cooling.filter({ hasText: '105 °C' }).locator('[data-label="Pump"]')
        ).toHaveText('P7');
        await expect(alarms.locator('svg circle')).toHaveCount(0);
        for (const key of ['P1', 'P7']) {
          const marker = page.locator(
            `[data-bay="${key}"] [data-alarm-marker]`
          );
          const motor = page.locator(
            `[data-bay="${key}"] rect[data-state-body]`
          );
          expect(
            (await marker.boundingBox())!.y +
              (await marker.boundingBox())!.height
          ).toBeLessThan((await motor.boundingBox())!.y);
        }
        const fault = page.locator('[data-bay="P2"]');
        await expect(fault.locator('[data-impeller]')).toHaveCount(1);
        const glyph = (await fault.locator('[data-fault-icon]').boundingBox())!;
        const motor = (await fault
          .locator('rect[data-state-body]')
          .boundingBox())!;
        expect(glyph.y).toBeGreaterThan(motor.y);
        expect(glyph.y + glyph.height).toBeLessThan(motor.y + motor.height);
        await expect(page.locator('[data-forebay-tank]')).toHaveCount(1);
      } else {
        await expect(
          alarms.getByRole('columnheader', { name: 'Pump', exact: true })
        ).toHaveCount(0);
        const callouts = page.locator('[data-unit-callouts] button[data-part]');
        await expect(callouts).toHaveCount(13);
        const warning = page.locator(
          '[data-unit-callouts] button[data-part="PS-THRUST-BRG"]'
        );
        await expect(warning).toHaveAccessibleName(
          /5\. Thrust bearing.*82 °C.*Warning/
        );
        const critical = page.locator(
          '[data-unit-callouts] button[data-part="PS-WATER-COOLING"]'
        );
        await expect(critical).toHaveAccessibleName(
          /7\. Pump cooling water.*105 °C.*Critical/
        );
        if (!phone) {
          for (const callout of await callouts.all())
            await expect(callout).toBeInViewport({ ratio: 1 });
          const geometry = await page
            .locator('[data-unit-drawing]')
            .evaluate((node) => {
              const native = node.querySelector('g > svg') as SVGSVGElement;
              const scale = Math.hypot(
                native.getScreenCTM()!.a,
                native.getScreenCTM()!.b
              );
              return {
                painted: native.getBBox().width * scale,
                content: node
                  .closest('[data-unit-schematic]')!
                  .getBoundingClientRect().width
              };
            });
          expect(geometry.painted).toBeGreaterThanOrEqual(geometry.content / 2);
          expect(geometry.painted).toBeGreaterThanOrEqual(780);
        } else {
          await expect(page.getByText('Live', { exact: true })).toBeInViewport({
            ratio: 1
          });
          for (const column of await page
            .locator('[data-callout-column]')
            .all())
            await expect(column.locator('button').first()).toHaveAttribute(
              'data-alarm-attention',
              'unacknowledged'
            );
        }
        const markers = page.locator('[data-part-markers] > g');
        const overlaps = await markers.evaluateAll((nodes) =>
          nodes.flatMap((node, index) =>
            nodes.slice(index + 1).flatMap((other) => {
              const a = node
                .querySelector('[data-marker-backing]')!
                .getBoundingClientRect();
              const b = other
                .querySelector('[data-marker-backing]')!
                .getBoundingClientRect();
              return Math.hypot(
                a.x + a.width / 2 - b.x - b.width / 2,
                a.y + a.height / 2 - b.y - b.height / 2
              ) <
                (a.width + b.width) / 2 + 1
                ? [
                    `${node.getAttribute('data-part')}/${other.getAttribute('data-part')}`
                  ]
                : [];
            })
          )
        );
        expect(overlaps).toEqual([]);
        await readable(warning.locator('[data-part-summary]'));
        await readable(critical.locator('[data-part-summary]'));
      }
      // Compare the actual state stroke with its painted fill, not just the page background.
      for (const selector of screen === 'station'
        ? [
            '[data-bay="P1"] rect[data-state-body]',
            '[data-bay="P2"] rect[data-state-body]'
          ]
        : [
            '[data-unit-drawing] #part-motor > rect',
            '[data-unit-drawing] #part-casing > circle:first-child'
          ]) {
        const ratio = await page.locator(selector).evaluate((node) => {
          const canvas = document.createElement('canvas');
          canvas.width = canvas.height = 1;
          const context = canvas.getContext('2d')!;
          const luminance = (color: string) => {
            context.fillStyle = color;
            context.fillRect(0, 0, 1, 1);
            return [...context.getImageData(0, 0, 1, 1).data]
              .slice(0, 3)
              .reduce((sum, value, i) => {
                const s = value / 255;
                return (
                  sum +
                  (s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4) *
                    [0.2126, 0.7152, 0.0722][i]
                );
              }, 0);
          };
          const style = getComputedStyle(node);
          const a = luminance(style.stroke);
          const b = luminance(style.fill);
          return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        });
        expect(ratio).toBeGreaterThanOrEqual(3);
      }
    });
  }

  test(`follow-up 2 Health ages and keyboard reasons ${scheme}`, async ({
    page
  }, info) => {
    await openHealth(page, {
      scheme,
      nativeTheme: true,
      compactChrome: true,
      signals: [
        ...HEALTH_SIGNALS,
        {
          id: 207,
          key: '/failed-history',
          name: 'Failed history',
          value: 12,
          unit: 'bar',
          trend: { reason: 'SOURCE_TIMEOUT' }
        }
      ]
    });
    const table = signalTable(page);
    const row = (name: string) =>
      table.getByRole('row').filter({
        has: page.getByRole('button', {
          name: `Inspect signal: ${name}`,
          exact: true
        })
      });
    const summaryTime = page
      .getByTestId('health-summary')
      .locator('time')
      .last();
    const rowTime = row('Bypass flow').locator('time');
    await expect(rowTime).toHaveAttribute(
      'datetime',
      (await summaryTime.getAttribute('datetime'))!
    );
    expect((await rowTime.textContent())!.split(' · ').at(-1)).toBe(
      (await summaryTime.textContent())!.split(' · ').at(-1)
    );
    await expect(rowTime).toContainText('just now');
    await expect(
      table
        .locator('time')
        .locator('..')
        .filter({ has: page.locator('button') })
    ).toHaveCount(0);
    const unit = row('Motor DE vibration (raw)').getByRole('button', {
      name: '1834',
      exact: true
    });
    await unit.focus();
    await expect(page.getByRole('tooltip')).toContainText(
      'Unit not reported by the plant'
    );
    await expect(unit).toHaveText('1834');
    await page.keyboard.press('Escape');
    for (const name of [
      'Seal flush flow',
      'Cooling water flow',
      'Winding temperature'
    ]) {
      if (info.project.name.endsWith('phone'))
        await row(name)
          .getByRole('button', { name: `Details for ${name}`, exact: true })
          .click();
      await page.mouse.move(0, 0);
      const reason = row(name).locator('td').last().getByRole('button');
      await expect(reason).toHaveText('—');
      await page.keyboard.press('Tab');
      await reason.focus();
      await expect(
        page.getByRole('tooltip').filter({
          hasText:
            name === 'Seal flush flow'
              ? 'No samples in window'
              : name === 'Cooling water flow'
                ? 'History unavailable'
                : 'Not returned'
        })
      ).toBeVisible();
      await expect(reason).toBeFocused();
      expect(
        await reason.evaluate((node) => (node as HTMLElement).tabIndex)
      ).toBe(0);
      await page.keyboard.press('Shift+Tab');
      await page.keyboard.press('Tab');
      await expect(reason).toBeFocused();
      await page.keyboard.press('Escape');
    }
    if (info.project.name.endsWith('phone'))
      await row('Failed history')
        .getByRole('button', {
          name: 'Details for Failed history',
          exact: true
        })
        .click();
    await expect(
      row('Failed history').locator('td').last().getByRole('button')
    ).toHaveText('Read failed');
  });

  test(`follow-up 2 Parts stock, borders, reasons and phone baseline ${scheme}`, async ({
    page
  }, info) => {
    await mockMachine(page, { assetType: 'pump' });
    const rows = components(true);
    await page.route('**/api/assets/registry/18/components/**', (route) =>
      route.fulfill({ json: { count: rows.length, results: rows } })
    );
    await page.route('**/api/assets/parts/?*', (route) =>
      route.fulfill({ json: { count: 0, results: [], next: null } })
    );
    await page.goto(
      `/playwright/s0-native.html?${new URLSearchParams({ surface: 'machine-shell', compactChrome: 'true', nativeTheme: 'true', scheme, entry: '/machines/machine/18/parts/' })}`
    );
    await expect(page.locator('[data-field="name"]')).toHaveCount(3);
    const stock = page.getByRole('link', { name: '6 in stock', exact: true });
    await expect(stock).toHaveText('6');
    for (const badge of await page
      .locator('[data-field="status"] .mantine-Badge-root')
      .all())
      await nonText(badge, badge.locator('..'));
    const failed = page.getByRole('button', {
      name: 'Open work: Read failed',
      exact: true
    });
    await failed.focus();
    await expect(page.getByRole('tooltip')).toContainText(
      'Work could not be checked'
    );
    await page.keyboard.press('Escape');
    if (info.project.name.endsWith('phone')) {
      await captureFromTop(page);
      const first = page.locator('table tbody tr').first();
      expect(
        (await first.boundingBox())!.y + (await first.boundingBox())!.height
      ).toBeLessThanOrEqual(844);
      const values = await Promise.all(
        ['status', 'open_work', 'stock'].map((field) =>
          first.locator(`[data-field="${field}"] > *`).first().boundingBox()
        )
      );
      const centres = values.map((box) => box!.y + box!.height / 2);
      expect(Math.max(...centres) - Math.min(...centres)).toBeLessThanOrEqual(
        2
      );
    }
  });
}
