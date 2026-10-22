import { type Page, expect } from '@playwright/test';
import { openPerformance } from '../s3/performanceFixtures';

/** Pause the current native Live control before deterministic S8 captures. */
export async function openPausedPerformance(
  page: Page,
  options: Parameters<typeof openPerformance>[1] = {}
) {
  const requests = await openPerformance(page, {
    ...options,
    settleInitialRead: true
  });
  const live = page.getByRole('switch', { name: /^Live updates:/ });
  await expect(live).toHaveCount(1);
  if (await live.isChecked()) {
    await live.focus();
    await live.press('Space');
    await expect(live).not.toBeChecked();
  }
  await expect(live).toHaveAccessibleName(/Live updates: (Paused|Read failed)/);
  await live.blur();
  await expect(page.getByRole('tooltip', { name: /^Paused:/ })).toBeHidden();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0', {
    timeout: 60000
  });
  return requests;
}
