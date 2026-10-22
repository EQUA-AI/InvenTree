import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { type Locator, type Page, expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  NOW,
  anomaly,
  openHealth,
  signalTable,
  trendCard
} from '../s3/healthFixtures';
import { signal } from '../s3/performanceFixtures';
import { contrast, nonText, textSweep } from './contrast';
import { drawnBoundary } from './hyperagentEvidence';
import { evidenceRoot } from './monitoringEvidence';
import { openPausedPerformance } from './monitoringFixtures';

nativeBaseline();
async function keyboardFocus(page: Page, target: Locator) {
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
      )
  );
  await target.focus();
  await expect(target).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(target).not.toBeFocused();
  // Document blur alone must not count as backward keyboard navigation.
  await expect
    .poll(() =>
      target.evaluate((el) => {
        const doc = el.ownerDocument;
        const active = doc.activeElement;
        return (
          doc.hasFocus() &&
          active?.isConnected &&
          active !== el &&
          active !== doc.body &&
          active !== doc.documentElement
        );
      })
    )
    .toBe(true);
  // Finish the key press and the next animation frame before reversing it.
  await page.evaluate(
    () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
  );
  await page.keyboard.press('Tab');
  await expect(target).toBeFocused();
}
async function focusCapture(
  page: Page,
  target: Locator,
  name: string,
  scheme: string
) {
  await keyboardFocus(page, target);
  const dir = resolve(evidenceRoot, 'browser/captures/focus/s8-01-part2-pass2');
  await mkdir(dir, { recursive: true });
  const box = (await target.boundingBox())!;
  const offset = await page.evaluate(() => ({ x: scrollX, y: scrollY }));
  await page.screenshot({
    path: resolve(dir, `${name}-${scheme}.png`),
    fullPage: true,
    animations: 'disabled',
    clip: {
      x: Math.max(0, box.x + offset.x - 6),
      y: Math.max(0, box.y + offset.y - 6),
      width: box.width + 12,
      height: box.height + 12
    }
  });
  const facts = await target.evaluate((el) => ({
    outline: getComputedStyle(el).outline,
    bounds: el.getBoundingClientRect().toJSON()
  }));
  await writeFile(
    resolve(dir, `${name}-${scheme}.json`),
    JSON.stringify(facts, null, 2)
  );
}
for (const scheme of ['light', 'dark'] as const) {
  test(`Health hierarchy, selection, clock, limits and focus ${scheme}`, async ({
    page
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript(() => {
      const url = new URL(location.href);
      url.searchParams.set('role', 'editor');
      history.replaceState(null, '', url);
    });
    await openHealth(page, {
      scheme,
      nativeTheme: true,
      compactChrome: true,
      anomalies: [anomaly()],
      summary: {
        state: 'warning',
        active_anomaly_count: 1,
        anomaly_counts: { critical: 0, warning: 1, info: 0 }
      }
    });
    const root = page.getByTestId('pump-health');
    const refresh = root.getByRole('button', { name: 'Refresh', exact: true });
    const x = (await refresh.boundingBox())!.x;
    // The live region is an <output> (implicit status role), not an element
    // with a role attribute.
    const announced = await root
      .locator('output[aria-atomic="true"]')
      .textContent();
    await refresh.click();
    await expect(root.locator('output[aria-atomic="true"]')).toHaveText(
      announced!
    );
    expect(
      await root
        .getByRole('heading', { name: 'Active anomalies', exact: true })
        .evaluate((el) => el.getBoundingClientRect().top)
    ).toBeLessThan(900);
    await focusCapture(page, refresh, 'refresh', scheme);
    await focusCapture(
      page,
      signalTable(page).getByRole('button', {
        name: 'Inspect signal: Discharge flow',
        exact: true
      }),
      'signal-name',
      scheme
    );
    await focusCapture(
      page,
      signalTable(page).getByRole('button', {
        name: 'Details for Discharge flow',
        exact: true
      }),
      'details',
      scheme
    );
    await focusCapture(
      page,
      root.getByRole('button', { name: /^Acknowledge\b/ }),
      'acknowledge',
      scheme
    );
    const selected = signalTable(page).locator('tr[aria-current="true"]');
    await expect(selected).toHaveCount(1);
    await nonText(selected, selected.locator('xpath=..'));
    const trend = trendCard(page);
    const heading = root.getByRole('heading', { name: 'Trend', exact: true });
    const chart = trend.locator('.recharts-wrapper');
    expect(
      (await chart.boundingBox())!.y - (await heading.boundingBox())!.y
    ).toBeLessThanOrEqual(200);
    const include = trend.getByRole('switch', {
      name: 'Include limits in scale',
      exact: true
    });
    await focusCapture(page, include, 'include-limits', scheme);
    await include.check({ force: true });
    await writeFile(
      resolve(evidenceRoot, `limits-dom-${scheme}.json`),
      JSON.stringify(
        await root.locator('.recharts-reference-line').evaluateAll((els) =>
          els.map((el) => ({
            html: el.outerHTML,
            section: el.closest('[data-section]')?.getAttribute('data-section'),
            visible: el.checkVisibility(),
            box: el.getBoundingClientRect().toJSON()
          }))
        ),
        null,
        2
      )
    );
    await drawnBoundary(trend.locator('.recharts-reference-line line').first());
    await expect(trend.getByTestId('threshold-key')).toContainText(
      'Warning 600 cusec · Critical 400 cusec'
    );
    await expect(include).toBeChecked();
    await root
      .getByRole('button', { name: 'Expand chart', exact: true })
      .click();
    const modal = page.getByRole('dialog', { name: 'Expanded signal trend' });
    await expect(modal).toBeVisible();
    await expect(modal).not.toContainText('Times: your clock');
    await drawnBoundary(modal.locator('.recharts-reference-line line').first());
    await page.keyboard.press('Escape');
    const badges = await root
      .locator('.mantine-Badge-root')
      .evaluateAll((els) =>
        els.map((el) => getComputedStyle(el).textTransform)
      );
    expect(badges.every((v) => v === 'none')).toBe(true);
    await openHealth(page, { scheme, nativeTheme: true, compactChrome: true });
    expect(
      (await page
        .getByTestId('pump-health')
        .getByRole('button', { name: 'Refresh', exact: true })
        .boundingBox())!.x
    ).toBe(x);
    await page.setViewportSize({ width: 390, height: 844 });
    const sourceScroll = page.getByRole('region', {
      name: 'Source connections; scroll for all columns',
      exact: true
    });
    await keyboardFocus(page, sourceScroll);
    await page.keyboard.press('ArrowRight');
    await expect
      .poll(() => sourceScroll.evaluate((el) => el.scrollLeft))
      .toBeGreaterThan(0);
    await writeFile(
      resolve(evidenceRoot, `source-scroll-keyboard-${scheme}.json`),
      JSON.stringify(
        await sourceScroll.evaluate((el) => ({
          scrollLeft: el.scrollLeft,
          scrollWidth: el.scrollWidth,
          width: el.clientWidth,
          tabIndex: (el as HTMLElement).tabIndex
        })),
        null,
        2
      )
    );
  });
  test(`Performance scales, navigation and keyboard controls ${scheme}`, async ({
    page
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.clock.setFixedTime(new Date(NOW));
    await openPausedPerformance(page, {
      scheme,
      nativeTheme: true,
      compactChrome: true,
      harness: 's3',
      signals: [
        signal('/dex/PUMP1_SPEED', 745, 'rpm', 'Shaft speed'),
        signal(
          '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1',
          80,
          'degC',
          'Winding 1',
          { state: 'normal', limits: { warn_max: 125, critical_max: 145 } }
        )
      ],
      harnessParams: { role: 'engineer' }
    });
    const root = page.getByTestId('pump-performance');
    // Performance retains source and cadence in a keyboard-readable info tooltip.
    const historyInformation = root.getByRole('button', {
      name: 'History information',
      exact: true
    });
    await expect(historyInformation).toHaveCount(1);
    await focusCapture(page, historyInformation, 'history-information', scheme);
    await expect(page.getByRole('tooltip')).toContainText('Fixture source');
    await expect(page.getByRole('tooltip')).toContainText('Sample interval');
    await expect(historyInformation).toHaveAccessibleDescription(
      /Fixture source.*Sample interval/
    );
    await expect(root.getByTestId('history-provenance')).toHaveCount(0);
    await expect(
      root.getByRole('button', { name: 'Show read details', exact: true })
    ).toHaveCount(0);
    await expect(root).not.toContainText('last poll attempt');
    await page.keyboard.press('Escape');
    const trendTrigger = root.locator('[data-kpi="speed"]').getByRole('button');
    await focusCapture(page, trendTrigger, 'trend-mark', scheme);
    const live = root.getByRole('switch', { name: /^Live updates:/ });
    await expect(live).toHaveAccessibleName('Live updates: Paused');
    await focusCapture(page, live, 'live', scheme);
    const track = root.locator('.mantine-Switch-track').first();
    const thumb = root.locator('.mantine-Switch-thumb').first();
    await nonText(track, track.locator('xpath=..'));
    await nonText(thumb, track);
    await live.check({ force: true });
    await expect(live).toHaveAccessibleName('Live updates: Live');
    await nonText(track, track.locator('xpath=..'));
    await nonText(thumb, track);
    await live.uncheck({ force: true });
    await expect(live).toHaveAccessibleName('Live updates: Paused');
    const section = root.getByRole('combobox', {
      name: 'Section',
      exact: true
    });
    await focusCapture(page, section, 'section-select', scheme);
    await section.click();
    const selected = page.getByRole('option', {
      name: 'Full overview',
      exact: true
    });
    await expect(selected).toHaveAttribute('data-checked', 'true');
    await nonText(selected, selected.locator('xpath=..'));
    expect((await contrast(selected)).ratio).toBeGreaterThanOrEqual(4.5);
    await page.keyboard.press('Escape');
    const key = root.getByTestId('threshold-key');
    await expect(key).toContainText('Warning 125 °C · Critical 145 °C');
    const windingLines = root.locator(
      '[data-section="Motor winding temperature"] .recharts-reference-line line'
    );
    await expect(windingLines).toHaveCount(0);
    const include = root.getByRole('switch', {
      name: 'Include limits in scale'
    });
    await include.check({ force: true });
    await writeFile(
      resolve(evidenceRoot, `limits-dom-${scheme}.json`),
      JSON.stringify(
        await root.locator('.recharts-reference-line').evaluateAll((els) =>
          els.map((el) => ({
            html: el.outerHTML,
            section: el.closest('[data-section]')?.getAttribute('data-section'),
            visible: el.checkVisibility(),
            box: el.getBoundingClientRect().toJSON()
          }))
        ),
        null,
        2
      )
    );
    await drawnBoundary(
      root
        .locator(
          '[data-section="Motor winding temperature"] .recharts-reference-line line'
        )
        .first()
    );
    await expect(include).toBeChecked();
    await expect(key).toContainText('Warning 125 °C · Critical 145 °C');
    await expect(root.locator('.recharts-bar-rectangle')).toHaveCount(0);
    const text = await textSweep(root);
    expect(text.filter((t) => t.ratio < 4.5)).toEqual([]);
    await page.setViewportSize({ width: 390, height: 844 });
    const nav = root.getByRole('button', { name: /^Channels ·/ });
    await nav.click();
    const targets = await root
      .locator(
        'button:visible,.mantine-Input-input:visible,.mantine-Switch-track:visible,.mantine-SegmentedControl-label:visible'
      )
      .evaluateAll((els) =>
        els
          .filter((el) => !el.closest('[data-disabled],[disabled]'))
          .map((el) => ({
            name: el.getAttribute('aria-label') || el.textContent,
            box: el.getBoundingClientRect().toJSON()
          }))
      );
    await writeFile(
      resolve(evidenceRoot, `touch-${scheme}.json`),
      JSON.stringify(targets, null, 2)
    );
    expect(
      targets.filter((t) => t.box.height < 40 || t.box.width < 40)
    ).toEqual([]);
    // The native date calendar is portalled outside the panel: include its
    // actual phone controls rather than only measuring the closed fields.
    await root.getByText('Custom', { exact: true }).click();
    await root.getByRole('button', { name: 'From', exact: true }).click();
    const calendar = page.locator('[data-dates-dropdown]:visible');
    await expect(calendar).toHaveCount(1);
    const calendarTargets = await calendar
      .locator('button:visible')
      .evaluateAll((els) =>
        els.map((el) => ({
          name: el.getAttribute('aria-label') || el.textContent,
          box: el.getBoundingClientRect().toJSON()
        }))
      );
    expect(calendarTargets.length).toBeGreaterThan(20);
    expect(
      calendarTargets.filter((t) => t.box.width < 40 || t.box.height < 40)
    ).toEqual([]);
    await writeFile(
      resolve(evidenceRoot, `touch-calendar-${scheme}.json`),
      JSON.stringify(calendarTargets, null, 2)
    );
    await page.keyboard.press('Escape');
  });
}
