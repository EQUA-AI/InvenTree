import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  authorityProjection,
  authorityRecord,
  mockAuthority
} from '../s4/authorityFixtures';
import {
  mockExecutionWork,
  openExecutionPanel
} from '../s4/workExecutionFixtures';
import { readable } from './contrast';

nativeBaseline();

for (const scheme of ['light', 'dark'] as const) {
  for (const state of [
    'absent',
    'missing',
    'recorded',
    'expired',
    'superseded',
    'future',
    'unverified'
  ] as const) {
    test(`phone authority dock uses ${state} native projection and keeps the caveat ${scheme}`, async ({
      page
    }, info) => {
      await page.setViewportSize({ width: 390, height: 844 });
      const value = authorityProjection();
      if (state === 'missing')
        value.operational = [authorityRecord('operating_permission')];
      if (!['absent', 'missing'].includes(state))
        value.physical = [
          authorityRecord('permit', {
            current: state !== 'superseded',
            effective_until:
              state === 'expired'
                ? '2026-01-01T00:00:00Z'
                : '2099-10-20T08:00:00Z',
            effective_from:
              state === 'future'
                ? '2099-10-19T07:00:00Z'
                : '2026-01-01T00:00:00Z',
            effective_state:
              state === 'expired'
                ? 'expired'
                : state === 'unverified'
                  ? 'awaiting_verification'
                  : 'within_window'
          })
        ];
      const fixture = await mockAuthority(page, value);
      if (state === 'absent')
        await mockExecutionWork(page, {
          ...fixture.overview,
          operational_authority: undefined
        });
      await openExecutionPanel(page, 'work', scheme);
      const boundary = page.getByTestId('work-phone-boundary');
      const label = ['absent', 'missing', 'superseded'].includes(state)
        ? 'Plan & Safety · —'
        : state === 'expired'
          ? 'Authority expired'
          : state === 'future'
            ? 'Authority not yet effective'
            : state === 'unverified'
              ? 'Authority awaiting verification'
              : 'Authority recorded';
      await expect(boundary).toContainText(label);
      await boundary.focus();
      await expect(page.getByRole('tooltip')).toContainText(
        'This record does not authorize work.'
      );
      await page.keyboard.press('Escape');
      await expect(boundary).toBeInViewport({ ratio: 1 });
      for (const heading of await page
        .locator('[data-stylish-heading]:visible')
        .all())
        await readable(heading);
      await boundary.click();
      await expect(
        page.getByRole('tab', { name: 'Plan & Safety', exact: true })
      ).toHaveAttribute('aria-selected', 'true');
      await page.screenshot({
        path: info.outputPath(`authority-${state}-${scheme}.png`)
      });
    });
  }
}

test('phone dock removes currency when a recorded authority expires while open', async ({
  page
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const instant = new Date('2026-10-21T08:00:00Z');
  await page.clock.install({ time: instant });
  const value = authorityProjection();
  value.physical = [
    authorityRecord('permit', { effective_until: '2026-10-21T08:10:00Z' })
  ];
  await mockAuthority(page, value);
  await openExecutionPanel(page, 'work');
  const boundary = page.getByTestId('work-phone-boundary');
  await expect(boundary).toContainText('Authority recorded');
  await expect(boundary).not.toContainText('expired');
  await page.clock.fastForward(10 * 60 * 1000);
  await expect(boundary).toContainText('Authority expired');
});
