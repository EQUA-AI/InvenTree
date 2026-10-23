import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import { observed } from '../mimic/layoutFixtures';
import { nativeBaseline } from '../s0/guards';
import { openHealth } from '../s3/healthFixtures';
import { nonText, readable, svgEdge } from './contrast';
import { captureFromTop } from './designFixtures';
import { mockOwnerLocations, ownerMimicFixture } from './ownerFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();
test.use({ hasTouch: true });

const capture = async (page: import('@playwright/test').Page, name: string) => {
  const directory = resolve('../../data/devscratch/ui-r2/owner/captures');
  await mkdir(directory, { recursive: true });
  await page.keyboard.press('Escape');
  await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
  await page.mouse.move(0, 0);
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await captureFromTop(page);
  await page.screenshot({
    path: resolve(directory, `${name}.png`),
    fullPage: true,
    animations: 'disabled'
  });
};

for (const scheme of ['light', 'dark'] as const) {
  test(`owner Health has one calm critical designation item and an accessible explanation ${scheme}`, async ({
    page
  }, info) => {
    await openHealth(page, { scheme, nativeTheme: true, compactChrome: true });
    const summary = page.getByTestId('health-summary');
    await expect(summary.getByText('Normal', { exact: true })).toBeVisible();
    const designation = summary.getByRole('button', {
      name: /^Normal · Assessed on 2 of 6 signals\. Critical signals: not designated\./
    });
    await expect(designation).toBeVisible();
    await expect(designation.locator('[data-tone="partial"]')).toHaveCount(1);
    await readable(designation);
    await expect(summary.getByRole('alert')).toHaveCount(0);
    await expect(
      page.getByText(/^Times: your clock|^Times: plant clock/)
    ).toHaveCount(0);
    await designation.hover();
    const explanation = page
      .getByRole('tooltip')
      .filter({ hasText: 'No critical-signal designation is supplied.' });
    await expect(explanation).toContainText(
      'Critical limits do not designate critical signals.'
    );
    await expect(explanation).toContainText(
      'Unassessed or missing evidence remains unknown'
    );
    await explanation.hover();
    await page.waitForTimeout(200);
    await expect(explanation).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(explanation).toBeHidden();
    await page.keyboard.press('Tab');
    await designation.focus();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab');
    await expect(designation).toBeFocused();
    await expect(explanation).toBeVisible();
    await expect(designation).toHaveAttribute(
      'aria-describedby',
      (await explanation.getAttribute('id'))!
    );
    await page.keyboard.press('Escape');
    await capture(
      page,
      `health-${scheme}-${info.project.name.endsWith('phone') ? 'phone' : 'desktop'}`
    );
  });

  for (const screen of ['station', 'pump'] as const) {
    test(`owner ${screen} uses real state, limits, acknowledgement and reduced motion ${scheme}`, async ({
      page
    }, info) => {
      await page.clock.setFixedTime(new Date(observed));
      let acknowledged = false;
      let operation = 'running';
      let flow = 2.4;
      let reporting = true;
      await page.route('**/api/machine-health/station/17/mimic/**', (route) => {
        const unit = new URL(route.request().url()).searchParams.get('unit');
        const data = ownerMimicFixture(unit);
        const bay = data.bays.find((item) => item.key === 'P7')!;
        bay.state = operation;
        data.reporting = reporting;
        const point = bay.points['/pd/P7/dv'];
        point.value = flow;
        if (unit === 'P7') data.points = bay.points;
        if (acknowledged)
          for (const alarm of data.alarms)
            alarm.anomaly_status = 'acknowledged';
        return route.fulfill({ json: data });
      });
      await page.goto(
        `/tests/mimic/index.html?${new URLSearchParams({ scheme, ...(screen === 'pump' ? { pump: 'P7' } : {}) })}`
      );
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      const running =
        screen === 'pump'
          ? page.locator('[data-unit-drawing]')
          : page.locator('[data-bay="P7"]');
      await expect(running).toHaveAttribute('data-operating-state', 'running');
      await expect(running.locator('[data-running-icon]')).toHaveCount(1);
      await expect(running.locator('[data-discharge-flow]')).toHaveCount(1);
      const flowPath = running.locator('[data-discharge-flow]');
      expect(
        await flowPath.evaluate((node) => getComputedStyle(node).animationName)
      ).not.toBe('none');
      await svgEdge(flowPath);
      const attention = page
        .locator('[data-attention="unacknowledged"]')
        .first();
      await expect(attention).toBeAttached();
      expect(
        await attention.evaluate(
          (node) => getComputedStyle(node).animationDuration
        )
      ).toBe('1s');
      expect(
        await attention.evaluate((node) => getComputedStyle(node).animationName)
      ).not.toBe('none');
      if (screen === 'pump') {
        for (const [part, condition] of [
          ['PS-THRUST-BRG', 'warning'],
          ['PS-WATER-COOLING', 'critical']
        ] as const) {
          const callout = page.locator(
            `[data-unit-callouts] button[data-part="${part}"]`
          );
          await expect(callout).toHaveAttribute('data-verdict', condition);
          await readable(callout.locator('[data-part-summary]'));
          await readable(callout.locator('[data-part-verdict]'));
          const marker = page.locator(
            `[data-part-markers] [data-part="${part}"]`
          );
          await expect(marker.locator('polygon')).toHaveCount(1);
          await readable(marker.locator('text'));
        }
        await expect(
          page.locator('[data-unit-callouts] button[data-part="PS-MOTOR"]')
        ).not.toHaveAttribute('data-verdict', /critical|warning|normal/);
        await expect(
          page.locator(
            '[data-unit-callouts] button[data-part="PS-MOTOR"] [data-part-summary]'
          )
        ).toContainText('200');
      } else {
        await expect(
          page.locator('[data-bay="P2"] [data-fault-icon]')
        ).toHaveCount(1);
        await expect(page.locator('[data-bay="P3"]')).toHaveAttribute(
          'data-operating-state',
          'stale'
        );
        await expect(page.locator('[data-bay="P4"]')).toHaveAttribute(
          'data-operating-state',
          'unknown'
        );
        await expect(page.locator('[data-bay="P5"]')).toHaveAttribute(
          'data-operating-state',
          'idle'
        );
        await expect(
          page.locator(
            '[data-bay="P3"] [data-discharge-flow], [data-bay="P4"] [data-discharge-flow]'
          )
        ).toHaveCount(0);
      }
      await page.emulateMedia({ reducedMotion: 'reduce' });
      expect(
        await flowPath.evaluate((node) => getComputedStyle(node).animationName)
      ).toBe('none');
      expect(
        await attention.evaluate((node) => getComputedStyle(node).animationName)
      ).toBe('none');
      expect(
        await attention.evaluate((node) => getComputedStyle(node).opacity)
      ).toBe('1');
      await capture(
        page,
        `${screen}-${scheme}-${info.project.name.endsWith('phone') ? 'phone' : 'desktop'}`
      );
      acknowledged = true;
      await page
        .getByRole('button', { name: 'Refresh', exact: true })
        .first()
        .click();
      await expect(
        page.locator('[data-attention="unacknowledged"]')
      ).toHaveCount(0);
      await expect(
        page.locator('[data-attention="acknowledged"]').first()
      ).toBeAttached();
      expect(
        await page
          .locator('[data-attention="acknowledged"]')
          .first()
          .evaluate((node) => getComputedStyle(node).animationName)
      ).toBe('none');
      await capture(
        page,
        `${screen}-acknowledged-${scheme}-${info.project.name.endsWith('phone') ? 'phone' : 'desktop'}`
      );
      flow = 0;
      await page
        .getByRole('button', { name: 'Refresh', exact: true })
        .first()
        .click();
      await expect(running.locator('[data-discharge-flow]')).toHaveCount(0);
      if (screen === 'pump') {
        flow = 2.4;
        for (const state of ['idle', 'fault', 'stale', 'unknown']) {
          operation = state;
          await page
            .getByRole('button', { name: 'Refresh', exact: true })
            .first()
            .click();
          await expect(running).toHaveAttribute('data-operating-state', state);
          await expect(
            running.locator('[data-discharge-flow], [data-running-icon]')
          ).toHaveCount(0);
        }
        operation = 'running';
        reporting = false;
        await page
          .getByRole('button', { name: 'Refresh', exact: true })
          .first()
          .click();
        await expect(running).toHaveAttribute('data-operating-state', 'stale');
        await expect(
          running.locator('[data-discharge-flow], [data-running-icon]')
        ).toHaveCount(0);
      }
    });
  }

  test(`owner location browser preserves full names, codes and hierarchy ${scheme}`, async ({
    page
  }, info) => {
    const nodes = await mockOwnerLocations(page);
    const phone = info.project.name.endsWith('phone');
    if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(
      `/playwright/s8-design.html?${new URLSearchParams({ screen: 'machines', entry: '/machines/index/sites/', scheme })}`
    );
    await expect(page.locator('[data-machine]')).toHaveCount(25);
    await expect(page.getByTestId('machine-page-count')).toHaveText('25 of 86');
    if (phone)
      await page
        .getByRole('button', { name: 'Choose location', exact: true })
        .click();
    await expect(
      page.getByRole('heading', {
        name: 'Browse top-level locations',
        exact: true
      })
    ).toBeVisible();
    const overview = page.getByRole('list', {
      name: 'Top-level locations',
      exact: true
    });
    await expect(overview.getByRole('listitem')).toHaveCount(7);
    await expect(
      overview
        .getByRole('listitem')
        .filter({ hasText: 'Cedar Creek Houston Regional Site' })
    ).toContainText('63 machines');
    await expect(
      page.getByRole('link', { name: /View unassigned machines/ })
    ).toHaveCount(0);
    for (const code of await overview.locator('code').all())
      await readable(code);
    if (phone) {
      const chooser = page.getByRole('button', {
        name: 'Choose location',
        exact: true
      });
      await expect(chooser).toBeVisible();
      await expect(chooser).toHaveAttribute('aria-expanded', 'true');
      for (const pk of [12, 20, 30]) {
        await page
          .getByRole('list', { name: 'Location drill-down' })
          .getByRole('button', { name: nodes.get(pk)!.name, exact: true })
          .click();
      }
      await expect(page.getByRole('tree')).toHaveCount(0);
      await expect(
        page
          .getByRole('list', { name: 'Location drill-down' })
          .getByRole('button', { name: nodes.get(37)!.name, exact: true })
      ).toBeVisible();
    } else {
      const tree = page.getByRole('tree', { name: 'Location tree' });
      await expect(tree.locator('button, a, input')).toHaveCount(0);
      for (const pk of [10, 11, 12, 20, 30]) {
        const item = tree.locator(`[role="treeitem"][data-value="${pk}"]`);
        await item.focus();
        await item.press('ArrowRight');
        await expect(item.locator('[role="treeitem"]').first()).toBeVisible();
      }
      const browser = page.locator('#machine-location-browser');
      expect((await browser.boundingBox())!.width).toBeGreaterThanOrEqual(360);
      for (const name of await tree
        .locator('[data-depth] > span:not([aria-hidden])')
        .filter({ hasText: /Cedar Creek|ACME Manufacturing/ })
        .all()) {
        await expect(name).toHaveCSS('text-overflow', 'clip');
        await expect(name).toHaveCSS('word-break', 'normal');
        expect(
          await name.evaluate(
            (node) => node.scrollWidth <= node.clientWidth + 1
          )
        ).toBe(true);
      }
      await expect(
        tree.locator('[data-value="30"] > [data-depth] [class*="treeDetail"]')
      ).toHaveText('· cc-feps');
      await expect(
        tree.locator('[data-value="30"] > [data-depth] [class*="treeDetail"]')
      ).toHaveCSS('clip-path', 'inset(50%)');
      const name = tree.locator('[role="treeitem"][data-value="30"]');
      await page.keyboard.press('Tab');
      await name.focus();
      await expect(
        page.getByRole('tooltip').filter({ hasText: 'cc-feps' })
      ).toContainText(nodes.get(30)!.name);
      await page.keyboard.press('Escape');
    }
    const search = page.getByRole('textbox', {
      name: 'Search locations',
      exact: true
    });
    await expect(search).toHaveAttribute('placeholder', 'Name or code');
    await expect(
      page.locator('label').filter({ hasText: 'Search locations' })
    ).toHaveCount(0);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(page.viewportSize()!.width);
    await capture(page, `locations-${scheme}-${phone ? 'phone' : 'desktop'}`);
    if (
      process.env.AIMMS_OWNER_CAPTURE === '1' &&
      (scheme === 'light' || !phone)
    ) {
      const name = phone
        ? '20-location-light-phone'
        : scheme === 'dark'
          ? '19-location-dark-desktop'
          : '10-location-light-desktop';
      const directory = resolve('../../data/devscratch/ui-r2/final');
      await mkdir(directory, { recursive: true });
      await captureFromTop(page);
      await page.screenshot({
        path: resolve(directory, `${name}.png`),
        animations: 'disabled'
      });
      await writeFile(
        resolve(directory, `${name}.json`),
        JSON.stringify(
          {
            synthetic: true,
            sourceRevision: process.env.AIMMS_SOURCE_REVISION,
            screen: 'location-browse',
            scheme,
            viewport: page.viewportSize(),
            capturedAt: new Date().toISOString(),
            renderer: 'Linux Chromium on owned WS3012'
          },
          null,
          2
        )
      );
    }
    await search.fill('cc-feps-main-pump-gallery');
    const result = page.getByRole('button', {
      name: /Cedar Creek Houston Regional Site.*Main Pump Gallery/
    });
    await expect(result).toBeVisible();
    await result.click();
    await expect(
      page.getByRole('heading', { name: 'Main Pump Gallery', exact: true })
    ).toBeVisible();
    if (phone)
      await expect(
        page.getByRole('heading', { name: 'Main Pump Gallery', exact: true })
      ).toBeFocused();
    await page
      .getByRole('button', { name: 'Location details', exact: true })
      .click();
    await expect(
      page.getByText('Room · cc-feps-main-pump-gallery', { exact: true })
    ).toBeVisible();
    const include = page.getByRole('switch', {
      name: 'Include sublocations',
      exact: true
    });
    const track = include.locator('+ span');
    for (const checked of [true, false]) {
      await include.setChecked(checked);
      await expect(include).toBeChecked({ checked });
      await expect(track).toHaveCSS('border-top-width', '1px');
      await expect(track).toHaveCSS('border-top-style', 'solid');
      await nonText(track, track.locator('..'));
    }
  });
}
