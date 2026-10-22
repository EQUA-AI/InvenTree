import { type Page, expect, test } from '@playwright/test';
import { nativeThemeFixtures } from '../s8/themeFixtures';

import { nativeBaseline } from '../s0/guards';
import { mockPositions } from './equipmentFixtures';

nativeBaseline();
nativeThemeFixtures();

// Equipment view reads the machine's position records once it mounts.
test.beforeEach(async ({ page }) => {
  await mockPositions(page);
});

async function openPanel(page: Page, scheme: 'light' | 'dark' = 'light') {
  await page.goto(`/playwright/s2-equipment.html?kind=panel&entry=${encodeURIComponent('/machines/machine/18/equipment/?view=model')}&scheme=${scheme}`);
  await expect(page.locator('[data-scene-phase="ready"]')).toBeVisible();
}

const canvas = (page: Page) => page.locator('canvas.aimms-twin-canvas');
const hotspot = (page: Page, label: string) =>
  page.locator(`.aimms-twin-hotspot[title="${label}"]`);
const selection = (page: Page) => page.locator('[data-selected-position]');
const listItem = (page: Page, id: string) =>
  page.locator(`[data-position="${id}"]`);
const button = (page: Page, name: string) =>
  page.getByRole('button', { name, exact: true });
// The mode control's radios are visually hidden; a user clicks the label.
const chooseMode = (page: Page, mode: string) =>
  page
    .getByRole('radiogroup', { name: 'Model view mode' })
    .getByText(mode, { exact: true })
    .click();
// The switch's own input covers its label and takes the click.
async function toggleLabels(page: Page) {
  const toggle = page.getByRole('switch', { name: 'Labels' });
  if (await toggle.isVisible()) return toggle.click();
  const item = page.getByRole('menuitem', { name: /^(Show|Hide) labels$/ });
  if (!(await item.isVisible())) await button(page, 'View').click();
  await item.click();
  await expect(item).toBeHidden();
}
async function clickControl(page: Page, name: string) {
  const control = button(page, name);
  if (await control.isVisible()) return control.click();
  await button(page, 'View').click();
  await page.getByRole('menuitem', { name, exact: true }).click();
}

/**
 * Where a part's hotspot anchors in the canvas, read from its leader line:
 * the scene sets it whether or not the hotspot itself is drawn (a part
 * hidden behind others keeps its anchor).
 */
const anchorOf = (page: Page, label: string) =>
  page.evaluate((title) => {
    const buttons = [...document.querySelectorAll('.aimms-twin-hotspot')];
    const lines = [
      ...document.querySelectorAll('.aimms-twin-hotspots svg line')
    ];
    const line =
      lines[buttons.findIndex((b) => b.getAttribute('title') === title)];
    const stage = document
      .querySelector('.aimms-twin-scene')!
      .getBoundingClientRect();
    return {
      x: Number(line.getAttribute('x1')) / stage.width,
      y: Number(line.getAttribute('y1')) / stage.height
    };
  }, label);

/** The model as drawn, after the scene's next frame has landed. */
async function frame(page: Page) {
  await page.waitForTimeout(350);
  // Keep exact scene-pixel comparisons; omit only the new HTML provenance
  // overlay, whose text rasterization changes when the page scrolls.
  return canvas(page).screenshot({
    animations: 'disabled',
    style:
      '[data-scene-phase] .mantine-Badge-root { visibility: hidden !important; }'
  });
}

async function expectOriginal(page: Page, initial: Buffer) {
  const restored = await frame(page);
  expect(restored.readUInt32BE(16), 'camera reset keeps the canvas width').toBe(
    initial.readUInt32BE(16)
  );
  expect(
    restored.readUInt32BE(20),
    'selection and scrolling keep the canvas height'
  ).toBe(initial.readUInt32BE(20));
  await test
    .info()
    .attach('initial-canvas', { body: initial, contentType: 'image/png' });
  await test
    .info()
    .attach('restored-canvas', { body: restored, contentType: 'image/png' });
  expect(restored.equals(initial)).toBe(true);
}

test('visual modes switch and switch back without losing the selection', async ({
  page
}, testInfo) => {
  await openPanel(page);
  const initial = await frame(page);
  await listItem(page, 'bearing').click();
  for (const mode of ['Cutaway', 'Exploded'] as const) {
    await chooseMode(page, mode);
    await expect(selection(page)).toHaveText(
      'Selected: Motor drive-end bearing'
    );
    await expect(hotspot(page, 'Motor drive-end bearing')).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    expect((await frame(page)).equals(initial)).toBe(false);
    await page.screenshot({
      path: testInfo.outputPath(`equipment-${mode.toLowerCase()}.png`),
      animations: 'disabled'
    });
  }
  await expect(page.locator('[data-mode-note="exploded"]')).toHaveCount(0);
  await page.getByText('Reference model', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText('Vertical reference model');
  await chooseMode(page, 'Assembled');
  await button(page, 'Clear selection').click();
  // Back where it started: the same assembly from the same direction.
  await expectOriginal(page, initial);
});

test('camera presets move the view and Reset returns it exactly, keeping the selection', async ({
  page
}) => {
  await openPanel(page);
  const initial = await frame(page);
  const seen = [initial];
  for (const view of ['Front', 'Side', 'Top']) {
    await clickControl(page, view);
    const shot = await frame(page);
    expect(seen.some((other) => other.equals(shot))).toBe(false);
    seen.push(shot);
  }
  await listItem(page, 'coupling').click();
  await clickControl(page, 'Zoom in');
  await clickControl(page, 'Reset view');
  await expect(selection(page)).toHaveText('Selected: Flexible coupling');
  await button(page, 'Clear selection').click();
  await expectOriginal(page, initial);
  // Overview is the same fitted view Reset returns to.
  await clickControl(page, 'Top');
  await clickControl(page, 'Overview');
  await expectOriginal(page, initial);
});

test('focus centres and fits a hidden internal part, keeping the selection', async ({
  page
}, testInfo) => {
  await openPanel(page);
  // In the assembled overview the bearing sits inside the motor housing.
  await expect(hotspot(page, 'Motor drive-end bearing')).toBeHidden();
  await listItem(page, 'bearing').click();
  await chooseMode(page, 'Cutaway');
  const before = await anchorOf(page, 'Motor drive-end bearing');
  const overview = await frame(page);
  await clickControl(page, 'Focus selection');
  const focused = await frame(page);
  expect(focused.equals(overview)).toBe(false);
  // Focus fits the part's bounds to the view: its anchor (a point on the
  // part, not its centre) lands well inside every edge, so nothing of it is
  // cut off.
  const after = await anchorOf(page, 'Motor drive-end bearing');
  for (const axis of ['x', 'y'] as const) {
    expect(after[axis]).toBeGreaterThan(0.25);
    expect(after[axis]).toBeLessThan(0.75);
  }
  expect(after).not.toEqual(before);
  await expect(selection(page)).toHaveText('Selected: Motor drive-end bearing');
  await page.screenshot({
    path: testInfo.outputPath('equipment-focus-bearing.png'),
    animations: 'disabled'
  });
});

test('the keyboard list and a click on the model choose the same position', async ({
  page
}) => {
  await openPanel(page);
  // Keyboard: reach the list item and activate it with Enter, then Space.
  await listItem(page, 'impeller').focus();
  await page.keyboard.press('Enter');
  await expect(selection(page)).toHaveText('Selected: Impeller');
  await expect(listItem(page, 'impeller')).toHaveAttribute(
    'aria-pressed',
    'true'
  );
  await expect(hotspot(page, 'Impeller')).toHaveAttribute(
    'aria-pressed',
    'true'
  );
  await listItem(page, 'seal').focus();
  await page.keyboard.press('Space');
  await expect(selection(page)).toHaveText('Selected: Mechanical seal');
  // Mouse on the model: the list follows.
  await page
    .getByRole('button', { name: '3. Select flexible coupling', exact: true })
    .click();
  await expect(listItem(page, 'coupling')).toHaveAttribute(
    'aria-pressed',
    'true'
  );
  await expect(listItem(page, 'seal')).toHaveAttribute('aria-pressed', 'false');
  // Every position is in the list, internal parts included.
  await expect(
    page.getByRole('list', { name: 'Model components' }).getByRole('listitem')
  ).toHaveCount(9);
});

test('labels hide and return, and clearing the selection keeps the mode', async ({
  page
}) => {
  await openPanel(page);
  await expect(hotspot(page, 'Electric motor')).toBeVisible();
  await toggleLabels(page);
  // The phone exposes the same state through the View menu.
  await expect(hotspot(page, 'Electric motor')).toBeHidden();
  await expect(hotspot(page, 'Electric motor')).toBeHidden();
  await toggleLabels(page);
  await expect(hotspot(page, 'Electric motor')).toBeVisible();
  await listItem(page, 'motor').click();
  await chooseMode(page, 'Exploded');
  await button(page, 'Clear selection').click();
  await expect(selection(page)).toHaveText('Selected: Complete pump train');
  await expect(
    page.getByRole('radio', { name: 'Exploded', exact: true })
  ).toBeChecked();
  await expect(button(page, 'Clear selection')).toHaveCount(0);
});

test('the wheel scrolls the page over the model until the model has focus', async ({
  page
}) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  await openPanel(page);
  const before = await frame(page);
  const box = (await canvas(page).boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, 400);
  await expect
    .poll(() => page.evaluate(() => window.scrollY))
    .toBeGreaterThan(0);
  expect((await frame(page)).equals(before)).toBe(true);
  // Once the model is focused, the wheel zooms it instead.
  await page.evaluate(() => window.scrollTo(0, 0));
  await canvas(page).focus();
  const focused = await frame(page);
  const box2 = (await canvas(page).boundingBox())!;
  await page.mouse.move(box2.x + box2.width / 2, box2.y + box2.height / 2);
  await page.mouse.wheel(0, -300);
  expect((await frame(page)).equals(focused)).toBe(false);
});

for (const size of [
  { width: 1100, height: 800, beside: false },
  { width: 1920, height: 1080, beside: true }
])
  test(`at ${size.width}x${size.height} the model keeps a useful size, the list follows the inspector and the inspector ${size.beside ? 'beside' : 'below'} it`, async ({
    page
  }, testInfo) => {
    test.skip(testInfo.project.name !== 's2-desktop', 'desktop layouts');
    await page.setViewportSize({ width: size.width, height: size.height });
    await openPanel(page);
    await expect(
      page.getByRole('tablist', { name: 'panel-tabs-asset-machine-detail' })
    ).toBeVisible();
    const view = (await canvas(page).boundingBox())!;
    const list = (await page
      .getByRole('list', { name: 'Model components' })
      .boundingBox())!;
    const inspector = (await page
      .getByLabel('Selected position', { exact: true })
      .boundingBox())!;
    expect(view.width).toBeGreaterThanOrEqual(560);
    const containerWidth = await page
      .locator('[data-scene-phase]')
      .locator('../..')
      .locator('..')
      .evaluate((el) => el.clientWidth);
    if (containerWidth >= 832) {
      expect(inspector.x).toBeGreaterThanOrEqual(view.x + view.width - 1);
      expect(list.x).toBeGreaterThanOrEqual(view.x + view.width - 1);
      expect(list.y).toBeGreaterThanOrEqual(inspector.y + inspector.height - 1);
    } else {
      expect(inspector.y).toBeGreaterThanOrEqual(view.y + view.height - 1);
      expect(inspector.y).toBeLessThan(size.height);
      expect(list.y).toBeGreaterThanOrEqual(inspector.y + inspector.height - 1);
    }
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(size.width);
    await page.screenshot({
      path: testInfo.outputPath(`equipment-${size.width}x${size.height}.png`),
      fullPage: true,
      animations: 'disabled'
    });
  });

test('the controls fit a phone without sideways scrolling', async ({
  page
}, testInfo) => {
  test.skip(testInfo.project.name !== 's2-phone', 'phone layout');
  for (const scheme of ['light', 'dark'] as const) {
    await openPanel(page, scheme);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(page.viewportSize()!.width);
    await page.screenshot({
      path: testInfo.outputPath(`equipment-phone-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
  }
});
