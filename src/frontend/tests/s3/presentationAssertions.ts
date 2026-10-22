import { type Locator, type Page, expect } from '@playwright/test';

export async function informationTooltip(
  page: Page,
  control: Locator,
  expected: string | RegExp
) {
  await expect(control).toBeFocused();
  await expect(control).toHaveAttribute('aria-describedby', /.+/);
  const description = await control.getAttribute('aria-describedby');
  const tooltip = page.locator(`[role="tooltip"][id="${description}"]`);
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toContainText(expected);
  await expect(control).toHaveAccessibleDescription(await tooltip.innerText());
  return tooltip;
}

export async function historyInformation(
  page: Page,
  scope: Page | Locator = page,
  expected: string | RegExp = /Fixture (source|historian)/
) {
  const control = scope.getByRole('button', {
    name: 'History information',
    exact: true
  });
  await page.mouse.move(0, 0);
  await page.keyboard.press('Tab');
  await control.focus();
  await expect(control).toBeFocused();
  await expect(control).toHaveAttribute('aria-describedby', /.+/);
  const description = await control.getAttribute('aria-describedby');
  const tooltip = page.locator(`[role="tooltip"][id="${description}"]`);
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toContainText(expected);
  await expect(control).toHaveAccessibleDescription(await tooltip.innerText());
  await control.blur();
  await page.mouse.move(0, 0);
  await expect(page.locator('[role="tooltip"]:visible')).toHaveCount(0);
}

export async function samplingInformation(
  page: Page,
  expected: string | RegExp
) {
  await page.getByText('Updated', { exact: true }).hover();
  const tooltip = page.getByRole('tooltip');
  await expect(tooltip).toContainText(expected);
  await page.mouse.move(0, 0);
  await expect(page.locator('[role="tooltip"]:visible')).toHaveCount(0);
}
