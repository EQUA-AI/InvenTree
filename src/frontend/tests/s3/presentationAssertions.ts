import { type Locator, type Page, expect } from '@playwright/test';

export async function historyInformation(page: Page, scope: Page | Locator = page, expected: string | RegExp = /Fixture (source|historian)/) {
  const control = scope.getByRole('button', { name: 'History information', exact: true });
  await page.keyboard.press('Tab');
  await control.focus();
  await expect(control).toBeFocused();
  const tooltip = page.getByRole('tooltip');
  await expect(tooltip).toContainText(expected);
  await expect(control).toHaveAccessibleDescription(expected);
  return tooltip;
}

export async function samplingInformation(page: Page, expected: string | RegExp) {
  await page.getByText('Updated', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText(expected);
  await page.mouse.move(0, 0);
}
