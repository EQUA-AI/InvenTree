import { type Page, expect, test } from '@playwright/test';
import { nativeThemeFixtures } from '../s8/themeFixtures';

import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { mockPositions } from './equipmentFixtures';

nativeBaseline();
nativeThemeFixtures();

// Equipment view reads the machine's position records once it mounts.
test.beforeEach(async ({ page }) => {
  await mockPositions(page);
});

async function openScene(
  page: Page,
  options: { view?: 'scene' | 'panel'; scheme?: 'light' | 'dark' } = {}
) {
  const query = new URLSearchParams({
    kind: options.view ?? 'scene',
    scheme: options.scheme ?? 'light'
  });
  await page.goto(`/playwright/s2-equipment.html?${query}`);
}

const ready = (page: Page) => page.locator('[data-scene-phase="ready"]');
const canvases = (page: Page) => page.locator('canvas.aimms-twin-canvas');
const fixture = (page: Page, name: string) =>
  page.getByRole('button', { name, exact: true });
// A hotspot by its part, whether or not it is drawn: the scene hides the
// hotspot of a part hidden behind others, and every hotspot when labels are off.
const hotspot = (page: Page, label: string) =>
  page.locator(`.aimms-twin-hotspot[title="${label}"]`);

test('the reference scene draws with a real WebGL renderer behind a same-height skeleton', async ({
  page
}, testInfo) => {
  for (const scheme of ['light', 'dark'] as const) {
    await openScene(page, { scheme });
    await expect(ready(page)).toBeVisible();
    await expect(canvases(page)).toHaveCount(1);
    const context = await canvases(page).evaluate((canvas) => {
      const gl =
        (canvas as HTMLCanvasElement).getContext('webgl2') ??
        (canvas as HTMLCanvasElement).getContext('webgl');
      return gl ? gl.getParameter(gl.VERSION) : null;
    });
    expect(context).toMatch(/WebGL/);
    const height = await ready(page).evaluate(
      (el) => el.getBoundingClientRect().height
    );
    expect(height).toBeGreaterThanOrEqual(420);
    expect(height).toBeLessThanOrEqual(680);
    await expect(
      page.getByRole('img', { name: /Conceptual vertical pump train/ })
    ).toBeVisible();
    await ready(page).screenshot({
      path: testInfo.outputPath(`equipment-scene-${scheme}.png`),
      animations: 'disabled'
    });
  }
});

test('selection, mode, labels and a telemetry refresh update the running scene without rebuilding it', async ({
  page
}) => {
  await openScene(page);
  await expect(ready(page)).toBeVisible();
  await canvases(page).evaluate((canvas) => {
    (canvas as HTMLCanvasElement & { s2Mark?: number }).s2Mark = 1;
  });
  for (const name of [
    'Fixture select bearing',
    'Fixture exploded',
    'Fixture toggle labels',
    'Fixture assembled',
    'Fixture select impeller'
  ])
    await fixture(page, name).click();
  for (let poll = 0; poll < 5; poll++)
    await fixture(page, 'Fixture telemetry poll').click();
  await expect(page.getByTestId('fixture-polls')).toHaveText('5');
  await expect(canvases(page)).toHaveCount(1);
  expect(
    await canvases(page).evaluate(
      (canvas) => (canvas as HTMLCanvasElement & { s2Mark?: number }).s2Mark
    )
  ).toBe(1);
  // The scene took the selection: its impeller hotspot is the pressed one.
  await expect(hotspot(page, 'Impeller')).toHaveAttribute(
    'aria-pressed',
    'true'
  );
  await expect(hotspot(page, 'Motor drive-end bearing')).toHaveAttribute(
    'aria-pressed',
    'false'
  );
});

test('remounting and changing the model never leave a second canvas behind', async ({
  page
}) => {
  await openScene(page);
  await expect(ready(page)).toBeVisible();
  for (let round = 0; round < 5; round++) {
    await fixture(page, 'Fixture toggle mount').click();
    await expect(canvases(page)).toHaveCount(0);
    await expect(page.locator('.aimms-twin-scene')).toHaveCount(0);
    await fixture(page, 'Fixture toggle mount').click();
    await expect(ready(page)).toBeVisible();
    await expect(canvases(page)).toHaveCount(1);
  }
  await canvases(page).evaluate((canvas) => {
    (canvas as HTMLCanvasElement & { s2Mark?: number }).s2Mark = 2;
  });
  await fixture(page, 'Fixture change model').click();
  await expect(ready(page)).toBeVisible();
  await expect(canvases(page)).toHaveCount(1);
  // A new model identity is a new scene.
  expect(
    await canvases(page).evaluate(
      (canvas) => (canvas as HTMLCanvasElement & { s2Mark?: number }).s2Mark
    )
  ).toBeUndefined();
});

test('an idle scene draws nothing: no animation implies the machine is running', async ({
  page
}) => {
  await page.addInitScript(() => {
    const original = window.requestAnimationFrame.bind(window);
    (window as unknown as { s2Frames: number }).s2Frames = 0;
    window.requestAnimationFrame = (callback) => {
      (window as unknown as { s2Frames: number }).s2Frames += 1;
      return original(callback);
    };
  });
  await openScene(page);
  await expect(ready(page)).toBeVisible();
  await page.waitForTimeout(500);
  const before = await page.evaluate(
    () => (window as unknown as { s2Frames: number }).s2Frames
  );
  await page.waitForTimeout(2000);
  const after = await page.evaluate(
    () => (window as unknown as { s2Frames: number }).s2Frames
  );
  expect(after - before).toBeLessThanOrEqual(2);
});

test('a renderer that cannot start fails locally and can be retried', async ({
  page
}) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    (window as unknown as { s2NoWebGL: boolean }).s2NoWebGL = true;
    (window as unknown as { s2Attempts: number }).s2Attempts = 0;
    HTMLCanvasElement.prototype.getContext = function (
      this: HTMLCanvasElement,
      kind: string,
      ...rest: unknown[]
    ) {
      if (kind.startsWith('webgl'))
        (window as unknown as { s2Attempts: number }).s2Attempts += 1;
      if (
        (window as unknown as { s2NoWebGL: boolean }).s2NoWebGL &&
        kind.startsWith('webgl')
      )
        return null;
      return (original as (...args: unknown[]) => unknown).call(
        this,
        kind,
        ...rest
      );
    } as typeof original;
  });
  await openScene(page);
  await expect(page.locator('[data-scene-phase="failed"]')).toBeVisible();
  await expect(
    page.getByText('3D view unavailable', { exact: true })
  ).toBeVisible();
  // Only this panel's own message: the scene's fallback and canvas are gone.
  await expect(page.locator('.aimms-twin-scene')).toHaveCount(0);
  await expect(canvases(page)).toHaveCount(0);
  // The rest of the page still works.
  await fixture(page, 'Fixture select bearing').click();
  await expect(page.getByTestId('fixture-selected')).toHaveText('bearing');
  // Retrying while WebGL is still missing fails again, cleanly: one message,
  // no scene or fallback of the renderer's own left underneath it.
  const attempts = () =>
    page.evaluate(
      () => (window as unknown as { s2Attempts: number }).s2Attempts
    );
  for (let attempt = 0; attempt < 2; attempt++) {
    const before = await attempts();
    await page
      .getByRole('button', { name: 'Retry 3D view', exact: true })
      .click();
    // The retry really ran: it asked for a WebGL context again, and failed.
    await expect.poll(attempts).toBeGreaterThan(before);
    await expect(page.locator('[data-scene-phase="failed"]')).toBeVisible();
    await expect(page.locator('.aimms-twin-scene')).toHaveCount(0);
    await expect(
      page.getByText('3D equipment view unavailable', { exact: true })
    ).toHaveCount(0);
  }
  await page.evaluate(() => {
    (window as unknown as { s2NoWebGL: boolean }).s2NoWebGL = false;
  });
  await page
    .getByRole('button', { name: 'Retry 3D view', exact: true })
    .click();
  await expect(ready(page)).toBeVisible();
  await expect(canvases(page)).toHaveCount(1);
  // The retried scene starts on the selection made while it was down.
  await expect(hotspot(page, 'Motor drive-end bearing')).toHaveAttribute(
    'aria-pressed',
    'true'
  );
});

test('the Equipment view says it is a reference model and owns the selection', async ({
  page
}, testInfo) => {
  for (const scheme of ['light', 'dark'] as const) {
    await openScene(page, { view: 'panel', scheme });
    await expect(ready(page)).toBeVisible();
    await expect(
      page.getByText(
        "Reference model: conceptual geometry, not this machine's verified design.",
        { exact: true }
      )
    ).toBeVisible();
    await expect(page.locator('[data-selected-position]')).toHaveText(
      'Selected: Complete pump train'
    );
    // A visible part's hotspot selects it; the panel, not the scene, owns
    // the selection and names it.
    await page
      .getByRole('button', { name: '3. Select flexible coupling', exact: true })
      .click();
    await expect(page.locator('[data-selected-position]')).toHaveText(
      'Selected: Flexible coupling'
    );
    await expect(hotspot(page, 'Flexible coupling')).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(page.viewportSize()!.width);
    await page.screenshot({
      path: testInfo.outputPath(`equipment-panel-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
  }
});

test("a pump's native machine page opens the Equipment view with the reference model", async ({
  page
}, testInfo) => {
  await mockMachine(page);
  await page.goto(
    '/playwright/s0-native.html?surface=machine&entry=/machines/machine/18/equipment/'
  );
  await expect(
    page.getByRole('tab', { name: 'Equipment view', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  await expect(ready(page)).toBeVisible();
  await expect(canvases(page)).toHaveCount(1);
  await expect(
    page.getByText(
      "Reference model: conceptual geometry, not this machine's verified design.",
      { exact: true }
    )
  ).toBeVisible();
  // Native panels stay where they were; Details remains the first.
  const tabs = await page.getByRole('tab').allInnerTexts();
  expect(tabs.slice(0, 2)).toEqual(['Details', 'Equipment view']);
  await page.screenshot({
    path: testInfo.outputPath('equipment-machine-page.png'),
    animations: 'disabled'
  });
  // Leaving the panel unmounts the scene; nothing lingers on other panels.
  await page.getByRole('tab', { name: 'Details', exact: true }).click();
  await expect(canvases(page)).toHaveCount(0);
});
