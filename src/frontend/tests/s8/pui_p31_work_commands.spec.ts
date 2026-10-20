import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { continuityWork, saveTransition } from '../s4/continuityFixtures';
import { openExecutionPanel } from '../s4/workExecutionFixtures';
import { closeoutWork } from '../s6/closeoutFixtures';
import { workScenario } from './workAccessibilityFixtures';

nativeBaseline();
for (const width of [1440, 390])
  for (const outcome of ['saved', 'refused'] as const) {
    test(`hold command ${outcome} recovers focus at ${width}px`, async ({
      page
    }) => {
      await page.setViewportSize({ width, height: 900 });
      const fixture = await closeoutWork(page);
      const work = continuityWork(false, true);
      fixture.setOverview(work);
      let writes = 0;
      await page.route('**/api/tasks/work-orders/73/hold/', (route) => {
        writes += 1;
        if (outcome === 'refused')
          return route.fulfill({
            status: 403,
            json: { detail: 'Synthetic command refused' }
          });
        const receipt = saveTransition(
          work,
          'hold',
          route.request().postDataJSON()
        );
        fixture.setOverview(work);
        return route.fulfill({ json: receipt });
      });
      await openExecutionPanel(page, 'work');
      const region = page.getByTestId('hold-resume-record');
      await region
        .getByLabel('Reason for hold', { exact: true })
        .fill('Synthetic retained hold reason');
      await region
        .getByRole('button', { name: 'Hold work record', exact: true })
        .focus();
      await page.keyboard.press('Enter');
      await expect(region).toContainText(
        outcome === 'saved' ? 'Hold recorded' : 'Operation refused'
      );
      await expect(region).toBeFocused();
      expect(writes).toBe(1);
    });
    test(`changed-condition command ${outcome} recovers focus at ${width}px`, async ({
      page
    }) => {
      await page.setViewportSize({ width, height: 900 });
      const fixture = await closeoutWork(page);
      fixture.setOverview(workScenario(true));
      let writes = 0;
      await page.route('**/api/tasks/work-orders/73/deviations/', (route) => {
        writes += 1;
        return route.fulfill({
          status: outcome === 'saved' ? 201 : 403,
          json:
            outcome === 'saved' ? { id: 999 } : { detail: 'Synthetic refusal' }
        });
      });
      await openExecutionPanel(page, 'plan', 'light', false, 'editor');
      await page
        .getByRole('button', {
          name: 'Record a changed condition',
          exact: true
        })
        .click();
      await page
        .getByLabel('Changed condition and reason', { exact: true })
        .fill('Synthetic changed condition');
      await page
        .getByRole('button', { name: 'Save deviation', exact: true })
        .focus();
      await page.keyboard.press('Enter');
      await expect(
        page.getByText(
          outcome === 'saved'
            ? 'Deviation saved to this work order.'
            : 'Save refused. No deviation was saved by this attempt. Your text is retained.',
          { exact: true }
        )
      ).toBeVisible();
      await expect(
        page.getByRole('group', {
          name: 'Changed condition record',
          exact: true
        })
      ).toBeFocused();
      expect(writes).toBe(1);
    });
    test(`reading command ${outcome} recovers focus at ${width}px`, async ({
      page
    }) => {
      await page.setViewportSize({ width, height: 900 });
      await closeoutWork(page);
      const rows: Record<string, unknown>[] = [];
      let writes = 0;
      await page.route(
        '**/api/tasks/work-orders/73/closeout/readings/',
        (route) => {
          if (route.request().method() === 'GET')
            return route.fulfill({ json: rows });
          writes += 1;
          if (outcome === 'refused')
            return route.fulfill({
              status: 403,
              json: { detail: 'Synthetic refusal' }
            });
          const row = {
            ...route.request().postDataJSON(),
            id: 53,
            verification_state: 'pending',
            warnings: []
          };
          rows.push(row);
          return route.fulfill({ status: 201, json: row });
        }
      );
      await openExecutionPanel(page, 'verify');
      await page.getByLabel('Label', { exact: true }).fill('Synthetic reading');
      await page.getByLabel('Value', { exact: true }).fill('0');
      await page.getByLabel('Unit', { exact: true }).fill('mm/s');
      await page
        .getByRole('button', { name: 'Add reading', exact: true })
        .focus();
      await page.keyboard.press('Enter');
      if (outcome === 'saved')
        await expect(page.getByLabel('Label', { exact: true })).toHaveValue('');
      else
        await expect(
          page.getByText(
            'Reading save refused. Your draft is retained. Check access and required fields before retrying.',
            { exact: true }
          )
        ).toBeVisible();
      await expect(
        page.getByRole('group', { name: 'Reading entry', exact: true })
      ).toBeFocused();
      expect(writes).toBe(1);
    });
  }

for (const kind of ['story', 'review', 'materials'] as const) {
  test(`Closeout ${kind} recovery retains command focus on phone`, async ({
    page
  }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await closeoutWork(page, kind === 'story' ? { captures: [] } : {});
    const pattern =
      kind === 'story'
        ? '**/closeout/captures/'
        : kind === 'review'
          ? '**/closeout/captures/12/decisions/'
          : '**/closeout/part-usage/refresh/';
    let writes = 0;
    await page.route(pattern, async (route) => {
      if (route.request().method() === 'GET') return route.fallback();
      writes += 1;
      await route.fulfill({
        status: kind === 'materials' ? 200 : 403,
        json: kind === 'materials' ? [] : { code: 'SYNTHETIC_REFUSAL' }
      });
    });
    await openExecutionPanel(page, 'closeout');
    const editor = page.getByTestId('closeout-capture-disclosure');
    if ((await editor.getAttribute('open')) === null)
      await editor.locator('summary').click();
    if (kind === 'story') {
      await page
        .getByLabel('What did you do?', { exact: true })
        .fill('Synthetic retained account');
      await page
        .getByLabel('Why voice is unavailable or unusable', { exact: true })
        .fill('Synthetic typed recovery scenario');
    } else
      await editor
        .getByText(kind === 'review' ? 'Review' : 'Parts', { exact: true })
        .click();
    const action = page.getByRole('button', {
      name:
        kind === 'story'
          ? 'Capture narrative'
          : kind === 'review'
            ? 'Record decisions'
            : 'Refresh from custody truth',
      exact: true
    });
    await expect(action).toBeEnabled();
    await action.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => writes).toBe(1);
    if (kind !== 'materials')
      await expect(page.getByTestId('draft-recovery')).toBeVisible();
    const name =
      kind === 'story'
        ? 'Closeout story recovery'
        : kind === 'review'
          ? 'Closeout field review'
          : 'Material reconciliation recovery';
    await expect(page.getByRole('group', { name, exact: true })).toBeFocused();
  });
}
