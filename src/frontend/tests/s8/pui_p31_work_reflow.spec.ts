import { type Locator, type Page, expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockWorkList, openWorkList } from '../s1/workListFixtures';
import { openExecutionPanel } from '../s4/workExecutionFixtures';
import { closeoutWork } from '../s6/closeoutFixtures';
import { phoneAssistant } from '../s7/phoneCompanionFixtures';
import {
  humanNames,
  noTooltipOverlap,
  scrollStops,
  unclippedBadges
} from './workAccessibilityAssertions';
import {
  filename,
  presentWorkPermissions,
  textZoom,
  workAttachment,
  workScenario
} from './workAccessibilityFixtures';

nativeBaseline();
const sizes = [
  { width: 1100, height: 800 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
  { width: 360, height: 844 },
  { width: 390, height: 844 },
  { width: 1440, height: 900, zoom: true }
];
const panels = [
  'Work',
  'Diagnose',
  'Plan & Safety',
  'Parts',
  'Verify & Return',
  'Closeout',
  'History'
];
async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    page.viewportSize()!.width
  );
}
async function uncovered(page: Page, control: Locator) {
  await control.focus();
  await control.evaluate((node) =>
    node.scrollIntoView({ block: 'center', inline: 'nearest' })
  );
  await expect(control).toBeInViewport({ ratio: 1 });
  await control.click({ trial: true });
  expect(
    await control.evaluate((node) => {
      const r = node.getBoundingClientRect();
      const points = [
        [r.x + r.width / 2, r.y + 2],
        [r.x + r.width / 2, r.bottom - 2],
        [r.x + 2, r.y + r.height / 2],
        [r.right - 2, r.y + r.height / 2],
        [r.x + r.width / 2, r.y + r.height / 2]
      ];
      return points.every(([x, y]) => {
        const hit = document.elementFromPoint(x, y);
        return !!hit && (hit === node || node.contains(hit));
      });
    })
  ).toBe(true);
  if (await control.evaluate((node) => Boolean(node.closest('main')))) {
    const dock = page.getByTestId('work-panel-navigation');
    if (
      await dock.evaluate((node) => getComputedStyle(node).position === 'fixed')
    ) {
      const actionBounds = await control.boundingBox();
      const dockBounds = await dock.boundingBox();
      expect(actionBounds!.y + actionBounds!.height).toBeLessThanOrEqual(
        dockBounds!.y
      );
    }
  }
}

for (const scheme of ['light', 'dark'] as const) {
  test(`Work clears a resized sticky header and exposes the full dependency state at 200% text ${scheme}`, async ({
    page
  }, info) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const fixture = await closeoutWork(page);
    fixture.setOverview(workScenario(true));
    await openExecutionPanel(page, 'plan', scheme);
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
    const panel = page.getByRole('tabpanel', { name: 'Plan & Safety' });
    await panel
      .getByRole('button', { name: 'Record a changed condition' })
      .click();
    await panel
      .getByLabel('Changed condition and reason', { exact: true })
      .fill('Synthetic retained draft; no command submitted.');
    await textZoom(page, true);
    const action = panel.getByRole('button', {
      name: 'Save deviation',
      exact: true
    });
    // Force the shared header into the taller layout seen with macOS metrics,
    // then resize it again to exercise the live measurement rather than a cap.
    const header = page.locator('[data-app-header]');
    for (const height of [240, 320]) {
      await header.evaluate((node, height) => {
        (node as HTMLElement).style.minHeight = `${height}px`;
      }, height);
      await expect
        .poll(() =>
          action.evaluate((node) =>
            Number.parseFloat(getComputedStyle(node).scrollMarginTop)
          )
        )
        .toBeGreaterThan(height);
      await uncovered(page, action);
      const bounds = await action.boundingBox();
      const headerBounds = await header.boundingBox();
      expect(bounds!.y).toBeGreaterThanOrEqual(
        headerBounds!.y + headerBounds!.height
      );
      await info.attach(`header-geometry-${height}`, {
        body: JSON.stringify({ header: headerBounds, action: bounds }),
        contentType: 'application/json'
      });
      await page.screenshot({
        path: info.outputPath(`header-${height}-text200-${scheme}.png`)
      });
    }
    const viewport = page.getByRole('group', {
      name: 'Dependencies table, scroll for all columns'
    });
    const state = viewport.locator('.mantine-Badge-root');
    // Scroll the viewport vertically, leaving its horizontal disclosure at the
    // initial position so automatic scrolling cannot conceal clipped state.
    await viewport.evaluate((node) =>
      node.scrollIntoView({ block: 'center', inline: 'nearest' })
    );
    expect(await viewport.evaluate((node) => node.scrollLeft)).toBe(0);
    await unclippedBadges(viewport);
    const visible = await state.evaluate((node) => {
      const viewport = node.closest('[data-work-scroll-viewport]')!;
      const b = viewport.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(node.querySelector('.mantine-Badge-label')!);
      return Array.from(range.getClientRects()).every(
        (r) =>
          r.left >= b.left &&
          r.right <= b.right &&
          r.top >= b.top &&
          r.bottom <= b.bottom
      );
    });
    expect(
      visible,
      'the complete dependency state survives the table viewport clip'
    ).toBe(true);
    await expect(state).toHaveText('In Progress');
    await page.screenshot({
      path: info.outputPath(`dependency-state-text200-${scheme}.png`)
    });
    expect(fixture.commands).toHaveLength(0);
  });
}

for (const size of sizes)
  for (const scheme of ['light', 'dark'] as const) {
    const label = `${size.width}x${size.height}${size.zoom ? '-text200' : ''}-${scheme}`;
    test(`workspace, forms, attachments and report ${label}`, async ({
      page
    }, info) => {
      test.setTimeout(240_000);
      await page.setViewportSize(size);
      const fixture = await closeoutWork(page);
      fixture.setOverview(workScenario(true));
      await workAttachment(page);
      await page.route('**/api/tasks/work-orders/73/materials/', (route) =>
        route.fulfill({ status: 404, json: { detail: 'Not found.' } })
      );
      await openExecutionPanel(page, 'work', scheme, false, 'editor');
      await presentWorkPermissions(page, { workorder: ['complete'] });
      await textZoom(page, size.zoom);
      for (const name of panels) {
        await page.getByRole('tab', { name, exact: true }).click();
        const panel = page.getByRole('tabpanel', { name, exact: true });
        await expect(panel).toBeVisible();
        await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
        await textZoom(page, size.zoom);
        await noOverflow(page);
        await scrollStops(page);
        const main = page.getByRole('main');
        await expect(main.getByRole('heading', { level: 1 })).toHaveCount(1);
        await humanNames(main);
        if (size.width < 768) {
          const back = await page.getByTestId('work-order-back').boundingBox();
          expect(back!.width).toBeGreaterThanOrEqual(44);
          expect(back!.height).toBeGreaterThanOrEqual(44);
        }
        await unclippedBadges(main);
        expect(
          await main.locator('h1, h2, h3, h4, h5, h6').evaluateAll((nodes) => {
            const levels = nodes
              .filter((n) => n.checkVisibility())
              .map((n) => Number(n.tagName.slice(1)));
            return levels.filter(
              (level, i) => i > 0 && level > levels[i - 1] + 1
            );
          })
        ).toEqual([]);
        expect(
          await main
            .locator('.mantine-Button-label, .mantine-Badge-label')
            .evaluateAll((nodes) =>
              nodes
                .filter(
                  (n) =>
                    n.checkVisibility() &&
                    (n.scrollWidth > n.clientWidth + 1 ||
                      n.scrollHeight > n.clientHeight + 1)
                )
                .map((n) => n.textContent)
            )
        ).toEqual([]);
        if (name === 'Diagnose') {
          const draft = page.getByTestId('native-observation-401');
          await draft
            .getByRole('button', {
              name: 'Record observation manually',
              exact: true
            })
            .click();
          await draft
            .getByLabel('Observed fact', { exact: true })
            .fill('Synthetic observation, no physical acceptance asserted.');
          await textZoom(page, size.zoom);
          await uncovered(
            page,
            draft.getByRole('button', {
              name: 'Save observation to request',
              exact: true
            })
          );
          await noOverflow(page);
          await page.screenshot({
            path: info.outputPath(`form-${label}.png`),
            fullPage: true
          });
        }
        if (name === 'Plan & Safety') {
          const criteria = panel.getByRole('button', {
            name: 'Instructions and sourced criteria (1)',
            exact: true
          });
          await criteria.focus();
          await page.keyboard.press('Enter');
          await expect(criteria).toBeFocused();
          await expect(criteria).toHaveAttribute('aria-expanded', 'true');
          await textZoom(page, size.zoom);
          await expect(
            panel.getByText(/Unit: mm\/s.*Minimum: 0.*Maximum: 4/)
          ).toBeVisible();
          await panel
            .getByRole('button', {
              name: 'Record a changed condition',
              exact: true
            })
            .click();
          await panel
            .getByLabel('Changed condition and reason', { exact: true })
            .fill(
              'Synthetic local changed-condition draft; no command submitted.'
            );
          await textZoom(page, size.zoom);
          await uncovered(
            page,
            panel.getByRole('button', { name: 'Save deviation', exact: true })
          );
          await page.screenshot({
            path: info.outputPath(`deviation-action-${label}.png`)
          });
          await noOverflow(page);
        }
        if (name === 'Verify & Return') {
          await expect(panel.getByTestId('verification-test-1')).toContainText(
            '0 mm/s'
          );
          await expect(
            panel.getByText(/Unit: mm\/s.*Minimum: 0.*Maximum: 4/)
          ).toBeVisible();
          const attempts = panel.getByRole('button', {
            name: 'Retained attempt events (1)',
            exact: true
          });
          await attempts.click();
          await textZoom(page, size.zoom);
          await expect(panel).toContainText(
            'Synthetic retained failed attempt'
          );
        }
        if (name === 'Closeout') {
          const update = panel.getByRole('button', {
            name: 'Update maintenance record',
            exact: true
          });
          await expect(update).toBeEnabled();
          await uncovered(page, update);
          await page.screenshot({
            path: info.outputPath(`action-${label}.png`)
          });
          await panel.getByText('Read report', { exact: true }).click();
          await textZoom(page, size.zoom);
          await noOverflow(page);
          const editor = panel.getByTestId('closeout-capture-disclosure');
          await editor.locator('summary').click();
          for (const step of ['Narrative', 'Review', 'Parts']) {
            await editor.getByText(step, { exact: true }).click();
            await textZoom(page, size.zoom);
            await noOverflow(page);
            await page.screenshot({
              path: info.outputPath(
                `closeout-${step.toLowerCase()}-${label}.png`
              ),
              fullPage: true
            });
          }
          await editor.locator('summary').click();
        }
        await panel.evaluate((node) => node.scrollIntoView({ block: 'start' }));
        await page.screenshot({
          path: info.outputPath(
            `${name.split(' ')[0].toLowerCase()}-${label}.png`
          ),
          fullPage: true
        });
        if (name === 'Plan & Safety') {
          const criteria = panel.getByRole('button', {
            name: 'Instructions and sourced criteria (1)',
            exact: true
          });
          await criteria.focus();
          await page.keyboard.press('Enter');
          await expect(criteria).toHaveAttribute('aria-expanded', 'false');
          await expect(criteria).toBeFocused();
        }
      }
      await page.getByRole('button', { name: /^Attachments \(/ }).click();
      await expect(
        page.getByRole('heading', { name: 'Work attachments', exact: true })
      ).toBeVisible();
      await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
      await textZoom(page, size.zoom);
      await noOverflow(page);
      await expect(
        page.getByRole('button', { name: `Preview ${filename}`, exact: true })
      ).toBeVisible();
      await page.screenshot({
        path: info.outputPath(`attachments-${label}.png`),
        fullPage: true
      });
      const preview = page.getByRole('button', {
        name: `Preview ${filename}`,
        exact: true
      });
      await preview.focus();
      await page.keyboard.press('Enter');
      await expect(
        page
          .getByTestId('media-evidence-modal')
          .getByRole('img', { name: filename, exact: true })
      ).toBeVisible();
      await textZoom(page, size.zoom);
      await noOverflow(page);
      await page.screenshot({ path: info.outputPath(`preview-${label}.png`) });
      await page.keyboard.press('Escape');
      await expect(preview).toBeFocused();
      await page
        .getByRole('button', { name: 'Close attachments', exact: true })
        .click();
      await expect(
        page.getByRole('button', { name: /^Attachments \(/ })
      ).toBeFocused();
      expect(fixture.commands).toHaveLength(0);
    });
    test(`My work and All work ${label}`, async ({ page }, info) => {
      await page.setViewportSize(size);
      await mockWorkList(page);
      await openWorkList(page, { scheme });
      await textZoom(page, size.zoom);
      for (const name of ['My work', 'All work']) {
        await page.getByText(name, { exact: true }).click();
        await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
        await expect(
          page.getByRole('link', { name: 'WO-73', exact: true })
        ).toBeVisible();
        await textZoom(page, size.zoom);
        await noOverflow(page);
        await scrollStops(page);
        await humanNames(page.getByRole('main'));
        await page.screenshot({
          path: info.outputPath(`${name.replace(' ', '-')}-${label}.png`),
          fullPage: true
        });
      }
    });
    test(`existing Work assistant ${label}`, async ({ page }, info) => {
      await page.setViewportSize(size);
      const fixture = await closeoutWork(page);
      fixture.setOverview(workScenario());
      await phoneAssistant(page);
      await openExecutionPanel(
        page,
        size.width >= 1100 ? 'closeout' : 'work',
        scheme
      );
      if (size.width >= 1100)
        await presentWorkPermissions(page, { workorder: ['complete'] });
      await textZoom(page, size.zoom);
      const trigger = page.getByRole('button', {
        name: 'Ask about this work',
        exact: true
      });
      await trigger.click();
      const splitter = page.getByRole('separator', {
        name: 'Resize chat panel',
        exact: true
      });
      const value = Number(await splitter.getAttribute('aria-valuenow'));
      expect(value).toBeGreaterThanOrEqual(
        Number(await splitter.getAttribute('aria-valuemin'))
      );
      expect(value).toBeLessThanOrEqual(
        Number(await splitter.getAttribute('aria-valuemax'))
      );
      const hint = page.getByTestId('ai-chat-work-focus');
      await expect(hint).toBeVisible();
      const context = hint.locator('summary');
      await context.focus();
      await page.keyboard.press('Enter');
      await expect(hint.locator('.mantine-Badge-label')).toContainText(
        workScenario().title
      );
      await textZoom(page, size.zoom);
      await page.screenshot({
        path: info.outputPath(`assistant-initial-${label}.png`)
      });
      expect(
        await hint.locator('.mantine-Badge-label').evaluate((node) => ({
          widthClipped: node.scrollWidth > node.clientWidth + 1,
          heightClipped: node.scrollHeight > node.clientHeight + 1
        }))
      ).toEqual({ widthClipped: false, heightClipped: false });
      await context.focus();
      await page.keyboard.press('Enter');
      await expect(context).toBeFocused();
      await noOverflow(page);
      await humanNames(page.getByTestId('ai-chat-drawer'));
      const message = page.getByRole('textbox', {
        name: 'Message',
        exact: true
      });
      await message.fill(
        'Synthetic draft to check enlarged composer controls.'
      );
      await uncovered(page, message);
      const send = page.getByTestId('send-ai-chat-message');
      await uncovered(page, send);
      await send.hover();
      await noTooltipOverlap(page, message);
      const boundary = page.getByTestId('ai-chat-physical-boundary');
      if (await boundary.isVisible()) await noTooltipOverlap(page, boundary);
      const messageBox = await message.boundingBox();
      const sendBox = await send.boundingBox();
      expect(sendBox!.y).toBeGreaterThanOrEqual(
        messageBox!.y + messageBox!.height
      );
      expect(messageBox!.width).toBeGreaterThan(
        (await page.getByTestId('ai-chat-composer').boundingBox())!.width * 0.75
      );
      if (size.zoom)
        expect(
          await message.evaluate((node) =>
            Number.parseFloat(getComputedStyle(node).fontSize)
          )
        ).toBeGreaterThanOrEqual(28);
      await page.screenshot({
        path: info.outputPath(`assistant-${label}.png`)
      });
      if (size.width >= 1100) {
        const update = page.getByRole('button', {
          name: 'Update maintenance record',
          exact: true
        });
        await uncovered(page, update);
        const resize = page.getByRole('separator', {
          name: 'Resize chat panel'
        });
        const initialWidth = await resize.getAttribute('aria-valuenow');
        await resize.focus();
        await page.keyboard.press('ArrowLeft');
        await expect(resize).not.toHaveAttribute(
          'aria-valuenow',
          initialWidth!
        );
        await uncovered(page, update);
        await noOverflow(page);
        await resize.focus();
        await page.keyboard.press('ArrowRight');
        await expect(resize).toHaveAttribute('aria-valuenow', initialWidth!);
        await page.screenshot({
          path: info.outputPath(`assistant-with-action-${label}.png`)
        });
      }
      await uncovered(page, page.getByTestId('close-ai-chat'));
      await page.screenshot({
        path: info.outputPath(`assistant-header-${label}.png`)
      });
      await message.focus();
      await page.keyboard.press('Escape');
      await expect(trigger).toBeFocused();
    });
  }
