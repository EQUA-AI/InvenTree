import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockWorkList, openWorkList } from '../s1/workListFixtures';
import { continuityWork } from '../s4/continuityFixtures';
import { openExecutionPanel } from '../s4/workExecutionFixtures';
import { closeoutWork } from '../s6/closeoutFixtures';
import { humanNames, visibleScrollFocus } from './workAccessibilityAssertions';

import {
  filename,
  presentWorkPermissions,
  textZoom,
  workAttachment,
  workScenario
} from './workAccessibilityFixtures';

nativeBaseline();
test('workflow arrow keys and record disclosure preserve focus', async ({
  page
}) => {
  await closeoutWork(page);
  await openExecutionPanel(page);
  const work = page.getByRole('tab', { name: 'Work', exact: true });
  await work.focus();
  await page.keyboard.press('End');
  await expect(
    page.getByRole('tab', { name: 'History', exact: true })
  ).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('tabpanel', { name: 'History', exact: true })
  ).toBeVisible();
  const details = page.getByRole('button', {
    name: 'Record details',
    exact: true
  });
  await details.focus();
  await page.keyboard.press('Enter');
  await expect(details).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Enter');
  await expect(details).toHaveAttribute('aria-expanded', 'false');
  await expect(details).toBeFocused();
});

for (const width of [1440, 390])
  for (const outcome of ['confirmed', 'failed', 'uncertain'] as const) {
    test(`final command retains keyboard focus on ${outcome} at ${width}px`, async ({
      page
    }) => {
      await page.setViewportSize({ width, height: 900 });
      await closeoutWork(page);
      await page.route('**/api/tasks/work-orders/73/complete/', (route) => {
        const body = route.request().postDataJSON();
        if (outcome === 'uncertain') return route.abort('timedout');
        if (outcome === 'failed')
          return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
        return route.fulfill({
          json: {
            work_order_id: 73,
            command: 'complete_work_order',
            event_id: 501,
            idempotency_key: body.idempotency_key,
            lifecycle_status: 'completed',
            lifecycle_version: body.expected_version + 1,
            metadata: { closeout_id: 301 }
          }
        });
      });
      await openExecutionPanel(page, 'closeout');
      await presentWorkPermissions(page, { workorder: ['complete'] });
      const action = page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      });
      await expect(action).toBeEnabled();
      await action.focus();
      await page.keyboard.press('Enter');
      const status = page.getByTestId('work-record-update');
      await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
        'data-update-status',
        outcome
      );
      await expect(status).toBeFocused();
      await page.keyboard.press('Tab');
      const next =
        outcome === 'confirmed'
          ? page.getByRole('link', {
              name: 'View Verify & Return',
              exact: true
            })
          : status.getByRole('button').first();
      await expect(next).toBeFocused();
    });
  }

for (const width of [1440, 390]) {
  test(`manual observation Escape and command results recover focus at ${width}px`, async ({
    page
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const fixture = await closeoutWork(page);
    const work = continuityWork();
    fixture.setOverview(work);
    const row = work.investigation!.requests.items![0];
    await page.route('**/investigation-requests/401/', (route) => {
      const body = route.request().postDataJSON();
      row.version += 1;
      const artifact = {
        id: 501,
        request: 401,
        kind: 'observation' as const,
        payload: body.fields.payload,
        attachment: null,
        supersedes: null,
        recorded_by: 11,
        recorded_by_name: 'Synthetic technician',
        created_at: '2026-10-20T09:00:00Z'
      };
      row.artifacts = [artifact];
      return route.fulfill({ json: { request: row, artifact_id: 501 } });
    });
    await openExecutionPanel(page, 'diagnose', 'dark', false, 'editor');
    const draft = page.getByTestId('native-observation-401');
    const trigger = draft.getByRole('button', {
      name: 'Record observation manually',
      exact: true
    });
    await trigger.focus();
    await page.keyboard.press('Enter');
    const text = draft.getByLabel('Observed fact', { exact: true });
    await text.fill('Synthetic observation draft retained.');
    await page.keyboard.press('Escape');
    await expect(trigger).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(text).toHaveValue('Synthetic observation draft retained.');
    const save = draft.getByRole('button', {
      name: 'Save observation to request',
      exact: true
    });
    await save.focus();
    await page.keyboard.press('Enter');
    await expect(draft).toContainText('Observation saved');
    await expect(draft).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(
      draft.getByRole('button', {
        name: 'Hide manual observation',
        exact: true
      })
    ).toBeFocused();
  });

  test(`keyboard reaches named table columns and filters return focus at ${width}px`, async ({
    page
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockWorkList(page);
    await openWorkList(page);
    const table = page.getByRole('group', {
      name: 'Work orders table, scroll for all columns',
      exact: true
    });
    await table.focus();
    if (width === 390) {
      await page.keyboard.press('ArrowRight');
      await expect
        .poll(() => table.evaluate((node) => node.scrollLeft))
        .toBeGreaterThan(0);
      const filters = page.getByRole('button', {
        name: 'Filters',
        exact: true
      });
      await filters.focus();
      await page.keyboard.press('Enter');
      const dialog = page.getByRole('dialog', {
        name: 'Work filters',
        exact: true
      });
      await expect(dialog).toBeVisible();
      const close = dialog.getByRole('button', {
        name: 'Close Work filters',
        exact: true
      });
      await expect(close).toHaveAccessibleName('Close Work filters');
      await expect(close).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
      await expect(filters).toBeFocused();
    }
    await table.focus();
    await expect(table).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(
      page.getByRole('link', { name: 'WO-73', exact: true })
    ).toBeFocused();
  });
}

for (const scheme of ['light', 'dark'] as const) {
  test(`nested dependency keyboard focus clears the measured phone dock with larger font metrics in ${scheme}`, async ({
    page
  }, info) => {
    await page.setViewportSize({ width: 390, height: 900 });
    const fixture = await closeoutWork(page);
    fixture.setOverview(workScenario());
    await openExecutionPanel(page, 'plan', scheme);
    const viewport = page.getByRole('group', {
      name: 'Dependencies table, scroll for all columns'
    });
    // Exercise wrapped links with different font metrics, rather than depending
    // on the host platform's system font to reproduce the clipped focus target.
    await viewport.evaluate((node) => {
      node.style.fontFamily = 'Arial, sans-serif';
      node.style.fontSize = '18px';
      node.style.lineHeight = '1.6';
    });
    const link = viewport.getByRole('link', {
      name: 'WO-74 · Synthetic sibling electrical check'
    });
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
    await page.evaluate(() => new Promise(requestAnimationFrame));
    const dock = page.getByTestId('work-panel-navigation');
    // Put the link inside the browser viewport, but beneath the fixed dock.
    // Its outer document position and inner horizontal position must both be
    // corrected by the product when focus enters the table link.
    await link.evaluate((node) => {
      const dock = document.querySelector(
        '[data-testid="work-panel-navigation"]'
      )!;
      const top = dock.getBoundingClientRect().top;
      window.scrollBy(0, node.getBoundingClientRect().top - top - 4);
    });
    const covered = await link.boundingBox();
    const dockBounds = await dock.boundingBox();
    expect(covered!.y).toBeGreaterThanOrEqual(dockBounds!.y);
    await page.screenshot({
      path: info.outputPath(`dependency-before-390x900-${scheme}.png`)
    });
    await viewport.evaluate((node) => node.focus({ preventScroll: true }));
    await page.keyboard.press('Tab');
    await expect(link).toBeFocused();
    const result = await link.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      const scroll = node.closest('[data-work-scroll-viewport]')!;
      const bounds = scroll.getBoundingClientRect();
      const navigation = document.querySelector(
        '[data-testid="work-panel-navigation"]'
      )!;
      const hit = document.elementFromPoint(
        rect.x + rect.width / 2,
        rect.y + rect.height / 2
      );
      return {
        rect: { left: rect.left, right: rect.right, top: rect.top },
        viewport: { left: bounds.left, right: bounds.right },
        bottom: rect.bottom,
        dockTop: navigation.getBoundingClientRect().top,
        visibleLeft: rect.left >= bounds.left,
        visibleRight: rect.right <= bounds.right,
        reachable: !!hit && (hit === node || node.contains(hit))
      };
    });
    await info.attach('dependency-focus-geometry', {
      body: JSON.stringify(result, null, 2),
      contentType: 'application/json'
    });
    expect(result.bottom).toBeLessThan(result.dockTop);
    expect(result.visibleLeft).toBe(true);
    expect(result.visibleRight).toBe(true);
    expect(result.reachable).toBe(true);
    await page.screenshot({
      path: info.outputPath(`dependency-focus-390x900-${scheme}.png`)
    });
    expect(fixture.commands).toHaveLength(0);
  });
}

for (const size of [
  { width: 1440, zoom: false },
  { width: 390, zoom: false },
  { width: 1440, zoom: true },
  { width: 1440, zoom: true, dark: true }
]) {
  const { width, zoom } = size;
  const scheme = size.dark ? 'dark' : 'light';
  test(`all visible Work panel actions have names, focus and an unobstructed keyboard path at ${width}px${zoom ? ` with 200% text ${scheme}` : ''}`, async ({
    page
  }, info) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width, height: 900 });
    const fixture = await closeoutWork(page);
    fixture.setOverview(workScenario());
    await page.route('**/api/tasks/work-orders/73/materials/', (route) =>
      route.fulfill({ status: 404, json: { detail: 'Not found.' } })
    );
    await openExecutionPanel(page, 'work', scheme);
    const main = page.getByRole('main');
    for (const name of [
      'Work',
      'Diagnose',
      'Plan & Safety',
      'Parts',
      'Verify & Return',
      'Closeout',
      'History'
    ]) {
      await main.getByRole('tab', { name, exact: true }).click();
      await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
      await textZoom(page, zoom);
      const expected = await main
        .locator(
          'button, a[href], input, textarea, select, summary, [tabindex]'
        )
        .evaluateAll((nodes) => {
          return nodes
            .filter(
              (n) =>
                n instanceof HTMLElement &&
                n.tabIndex >= 0 &&
                n.checkVisibility() &&
                !n.matches(':disabled') &&
                !n.closest('[inert]')
            )
            .map((n, i) => {
              n.setAttribute('data-p31-focus', String(i));
              return String(i);
            });
        });
      await page.keyboard.press('Tab');
      await main.locator('[data-p31-focus="0"]').focus();
      const seen: string[] = [];
      for (let i = 0; i < expected.length; i++) {
        const focused = await page.evaluate(() => {
          const node = document.activeElement as HTMLElement;
          const r = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          const hit = document.elementFromPoint(
            r.x + r.width / 2,
            r.y + r.height / 2
          );
          return {
            id: node.getAttribute('data-p31-focus'),
            tag: node.tagName,
            focus: style.outlineStyle !== 'none' || style.boxShadow !== 'none',
            reachable: !!hit && (hit === node || node.contains(hit)),
            scroll: node.matches('[data-work-scroll-viewport]'),
            height: r.height
          };
        });
        expect(
          focused.id,
          `${name}: keyboard left the Work surface`
        ).not.toBeNull();
        seen.push(focused.id!);
        if (
          ['BUTTON', 'A', 'INPUT', 'TEXTAREA', 'SUMMARY'].includes(focused.tag)
        ) {
          expect(
            focused.focus,
            `${name}: visible focus for ${focused.id}`
          ).toBe(true);
          expect(
            focused.reachable,
            `${name}: keyboard action ${focused.id} covered by fixed UI`
          ).toBe(true);
        }
        if (focused.scroll)
          await visibleScrollFocus(
            main.locator(`[data-p31-focus="${focused.id}"]`)
          );
        if (zoom && i === 0) {
          await page.screenshot({
            path: info.outputPath(
              `focus-header-${name.replaceAll(' ', '-')}.png`
            )
          });
        }
        await page.keyboard.press('Tab');
      }
      expect(seen).toEqual(expected);
      await main
        .locator('[data-p31-focus]')
        .evaluateAll((nodes) =>
          nodes.forEach((n) => n.removeAttribute('data-p31-focus'))
        );
    }
    expect(fixture.commands).toHaveLength(0);
  });
}

test('a pending final update does not take focus from a later keyboard action', async ({
  page
}) => {
  await closeoutWork(page);
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/tasks/work-orders/73/complete/', async (route) => {
    await gate;
    await route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
  });
  await openExecutionPanel(page, 'closeout');
  await presentWorkPermissions(page, { workorder: ['complete'] });
  const action = page.getByRole('button', {
    name: 'Update maintenance record',
    exact: true
  });
  await expect(action).toBeEnabled();
  await action.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
    'data-update-status',
    'pending'
  );
  const next = page.getByRole('link', {
    name: 'View Verify & Return',
    exact: true
  });
  await next.focus();
  release!();
  await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
    'data-update-status',
    'failed'
  );
  await expect(next).toBeFocused();
});

const attachmentSizes: { width: number; height: number; zoom?: boolean }[] = [
  { width: 1100, height: 800 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
  { width: 360, height: 844 },
  { width: 390, height: 844 },
  { width: 1440, height: 900, zoom: true }
];
for (const size of attachmentSizes)
  for (const scheme of ['light', 'dark'] as const) {
    const { width, height } = size;
    const label = `${width}x${height}${size.zoom ? '-text200' : ''}-${scheme}`;
    test(`native attachment table exposes every column to the keyboard ${label}`, async ({
      page
    }, info) => {
      test.setTimeout(240_000);
      await page.setViewportSize({ width, height });
      const fixture = await closeoutWork(page);
      fixture.setOverview(workScenario());
      let attachmentReads = 0;
      page.on('request', (request) => {
        if (
          new URL(request.url()).pathname.startsWith('/api/attachment/') &&
          request.method() === 'GET'
        )
          attachmentReads++;
      });
      await workAttachment(page);
      await openExecutionPanel(page, 'work', scheme);
      const trigger = page.getByRole('button', { name: /^Attachments \(/ });
      await trigger.click();
      const table = page.getByRole('group', {
        name: 'Work attachments table, scroll for all columns',
        exact: true
      });
      await expect(page.getByTestId('fixture-fetching')).toHaveText('0', {
        timeout: 30_000
      });
      await expect(table).toBeVisible({ timeout: 30_000 });
      await textZoom(page, size.zoom);
      await expect(
        page.getByRole('columnheader', { name: 'Actions', exact: true })
      ).toBeVisible();
      const beforeRefresh = attachmentReads;
      await page.getByTestId('table-refresh').focus();
      await page.keyboard.press('Enter');
      await expect.poll(() => attachmentReads).toBeGreaterThan(beforeRefresh);
      await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
      await page.keyboard.press('Tab');
      await table.focus();
      await expect(table).toBeFocused();
      await table.evaluate((node) => node.scrollIntoView({ block: 'center' }));
      await page.screenshot({
        path: info.outputPath(`attachment-columns-start-${label}.png`)
      });
      const extents = await table.evaluate((node) => ({
        content: node.scrollWidth,
        viewport: node.clientWidth,
        range: node.scrollWidth - node.clientWidth,
        overflow: getComputedStyle(node).overflowX
      }));
      await info.attach('attachment-table-extents', {
        body: JSON.stringify(extents),
        contentType: 'application/json'
      });
      await expect(table).toHaveAttribute(
        'tabindex',
        extents.range > 1 ? '0' : '-1'
      );
      if (extents.range > 1) await visibleScrollFocus(table);
      // A one-pixel collapsed table border is not an undisclosed column.
      if (width < 768) expect(extents.range).toBeGreaterThan(1);
      if (extents.range > 1) {
        await table.focus();
        await expect(table).toBeFocused();
        await page.keyboard.press('ArrowRight');
        await expect
          .poll(() => table.evaluate((node) => node.scrollLeft))
          .toBeGreaterThan(0);
        await table.evaluate((node) => {
          node.scrollLeft = node.scrollWidth;
        });
        await page.screenshot({
          path: info.outputPath(`attachment-columns-end-${label}.png`)
        });
      }
      const preview = page.getByRole('button', {
        name: `Preview ${filename}`,
        exact: true
      });
      await preview.focus();
      await expect(preview).toBeFocused();
      await preview.click({ trial: true });
      const previewHint = page
        .getByRole('tooltip')
        .filter({ hasText: `Preview ${filename}` });
      await expect(previewHint).toBeVisible();
      await textZoom(page, size.zoom);
      await expect(previewHint).toHaveText(`Preview ${filename}`);
      const main = page.getByRole('main');
      await humanNames(main);
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth)
      ).toBe(width);
      await page.screenshot({
        path: info.outputPath(`preview-tooltip-${label}.png`)
      });
      await page
        .getByRole('button', { name: 'Close attachments', exact: true })
        .click();
      await expect(trigger).toBeFocused();
      expect(fixture.commands).toHaveLength(0);
    });
  }
