import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  expectNoPageOverflow,
  mockExecutionWork,
  onlyReads,
  openExecutionPanel,
  panelRegion
} from '../s4/workExecutionFixtures';

import { closeoutWork } from '../s6/closeoutFixtures';
import { workScenario } from '../s8/workAccessibilityFixtures';

nativeBaseline();

const panels = [
  ['work', 'Work'],
  ['diagnose', 'Diagnose'],
  ['plan', 'Plan & Safety'],
  ['parts', 'Parts'],
  ['verify', 'Verify & Return'],
  ['closeout', 'Closeout'],
  ['history', 'History']
] as const;

for (const scheme of ['light', 'dark'] as const) {
  for (const [panel, label] of panels) {
    test(`Work wording and layout ${panel} ${scheme}`, async ({
      page
    }, info) => {
      test.skip(scheme === 'dark' && info.project.name.endsWith('phone'));
      const requests =
        panel === 'closeout'
          ? (await closeoutWork(page, { ready: true })).commands
          : await mockExecutionWork(page, workScenario());
      if (panel === 'verify') {
        await page.route(
          '**/api/tasks/work-orders/73/closeout/readings/',
          (route) => route.fulfill({ json: [] })
        );
      }
      await openExecutionPanel(page, panel, scheme);
      await expect(panelRegion(page, label)).toBeVisible();
      await expectNoPageOverflow(page);
      const region = panelRegion(page, label);
      if (
        panel === 'parts' &&
        !process.env.AIMMS_CAPTURE_DIR?.includes('/before')
      ) {
        await expect(
          region.getByRole('button', {
            name: 'Notes: No allocation note recorded'
          })
        ).toHaveText('—');
      }
      await region.evaluate((node) => {
        const header =
          document.querySelector('[data-app-header]')?.getBoundingClientRect()
            .height ?? 72;
        const navigation =
          innerWidth >= 500
            ? (document
                .querySelector('[data-testid="work-panel-navigation"]')
                ?.getBoundingClientRect().height ?? 0)
            : 0;
        window.scrollTo(
          0,
          node.getBoundingClientRect().top + scrollY - header - navigation - 12
        );
      });
      await expect
        .poll(() => region.evaluate((node) => node.getBoundingClientRect().top))
        .toBeLessThan(page.viewportSize()!.height / 2);
      await page.screenshot({
        path: resolve(
          import.meta.dirname,
          process.env.AIMMS_CAPTURE_DIR ??
            '../../../../data/devscratch/r8-polish/captures/after',
          `tab-${panel}-${info.project.name}-${scheme}.png`
        ),
        fullPage: false,
        animations: 'disabled'
      });
      expect(onlyReads(requests)).toBe(true);
      if (!process.env.AIMMS_CAPTURE_DIR?.includes('/before')) {
        const visible = await panelRegion(page, label).innerText();
        expect(visible).not.toMatch(
          /Extraction is not a verified fact|This account does not establish permission|The preview exports the displayed|Operational tasks retain their own|Service restoration is not established by Work completion|Not supplied by this server|Full service, required capacity/
        );
      }
    });
  }
}

test('missing Work facts and authority caveat remain available on focus', async ({
  page
}) => {
  const requests = await mockExecutionWork(page, workScenario());
  await openExecutionPanel(page, 'work');
  const missing = panelRegion(page, 'Work').getByRole('button', {
    name: 'Repair outcome: No outcome recorded'
  });
  await expect(missing).toHaveText('—');
  await missing.focus();
  await expect(page.getByRole('tooltip')).toHaveText(
    'Repair outcome: No outcome recorded'
  );
  await page.keyboard.press('Escape');
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  const boundary = page.getByTestId('work-phone-boundary');
  await boundary.focus();
  await expect(page.getByRole('tooltip')).toContainText(
    'This record does not authorize work.'
  );
  await boundary.click();
  await expect(
    page.getByRole('tab', { name: 'Plan & Safety', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  expect(onlyReads(requests)).toBe(true);
});
