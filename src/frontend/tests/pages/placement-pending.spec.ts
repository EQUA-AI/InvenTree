import { type Page, expect, test } from '@playwright/test';

const row = {
  pk: 17,
  client: 3,
  name: 'Synthetic placement pump',
  description: '',
  active: true,
  asset_type: 'equipment',
  parent: null,
  parent_name: null,
  source_key: '',
  location: '',
  manufacturer: '',
  model: '',
  serial: 'S5-local',
  created_at: '',
  updated_at: '',
  placement_version: 2,
  physical_location: {
    pk: 8,
    client: 3,
    parent: null,
    name: 'Synthetic site',
    code: 's5',
    kind: 'site',
    description: '',
    timezone: 'UTC',
    effective_timezone: 'UTC',
    archived: false,
    version: 1,
    path: [{ pk: 8, name: 'Synthetic site' }],
    has_children: false
  },
  placement_sync: {
    schema_version: 'aimms.placement.sync.v1',
    state: 'pending',
    machine: '17',
    client: '3',
    location: '8',
    native_version: '2',
    source_fence: '5'
  }
};

test('default-off stays native; fresh backend metadata appears in the actual card and list', async ({
  page
}) => {
  let enrolled = false;
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/assets/locations/**', async (route) => {
    const context = route.request().url().includes('/context/');
    const { placement_sync, ...native } = row;
    await route.fulfill({
      json: context
        ? {
            workspaces: [{ pk: 3, name: 'Synthetic workspace' }],
            can_add: false,
            can_change: true
          }
        : { count: 1, results: [enrolled ? row : native], next: null }
    });
  });
  await page.goto('/__s5.html');
  await expect(page.getByText(row.name, { exact: true })).toBeVisible();
  await expect(page.locator('[data-placement-sync]')).toHaveCount(0);
  enrolled = true;
  await page.reload();
  await expect(page.locator('[data-placement-sync="pending"]')).toHaveCount(2);
  await expect(
    page.getByRole('button', { name: 'Move machine', exact: true })
  ).toBeEnabled();
  expect(errors).toEqual([]);
  await page.screenshot({
    path: `${process.env.S5_UI_STATE}/desktop-pending.png`,
    fullPage: true
  });
});

type Mode = 'off' | 'pending' | 'current' | 'unqualified' | 'denied';
async function source(
  page: Page,
  mode: () => Mode,
  wait?: () => Promise<void>
) {
  await page.route('**/api/assets/locations/**', async (route) => {
    if (route.request().url().includes('/context/')) {
      await route.fulfill({
        json: {
          workspaces: [{ pk: 3, name: 'Synthetic workspace' }],
          can_add: false,
          can_change: true
        }
      });
      return;
    }
    if (wait) await wait();
    const state = mode();
    if (state === 'denied') {
      await route.fulfill({
        status: 403,
        json: { detail: 'Synthetic current scope denied' }
      });
      return;
    }
    const { placement_sync, ...native } = row;
    const machine =
      state === 'off'
        ? native
        : { ...row, placement_sync: { ...placement_sync, state } };
    await route.fulfill({ json: { count: 1, results: [machine], next: null } });
  });
}

test('a refetch cannot leave cached current badges qualified; denied reads hide old rows', async ({
  page
}) => {
  let mode: Mode = 'current';
  let gate: Promise<void> | undefined;
  let release = () => {};
  await source(
    page,
    () => mode,
    async () => {
      if (gate) await gate;
    }
  );
  await page.goto('/__s5.html');
  await expect(page.locator('[data-placement-sync="current"]')).toHaveCount(2);
  gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page
    .getByRole('button', { name: 'Fixture refresh authorized records' })
    .click();
  await expect(page.locator('[data-placement-sync="checking"]')).toHaveCount(2);
  await expect(page.locator('[data-placement-sync="current"]')).toHaveCount(0);
  mode = 'pending';
  release();
  gate = undefined;
  await expect(page.locator('[data-placement-sync="pending"]')).toHaveCount(2);
  mode = 'denied';
  await page
    .getByRole('button', { name: 'Fixture refresh authorized records' })
    .click();
  await expect(page.locator('[data-placement-sync]')).toHaveCount(0);
  await expect(page.getByText(row.name, { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Move machine', exact: true })
  ).toHaveCount(0);
});

test('reopen, a separate browser session and identity change reread rather than retain a sync flag', async ({
  page,
  browser
}) => {
  let mode: Mode = 'pending';
  await source(page, () => mode);
  await page.goto('/__s5.html');
  await expect(page.locator('[data-placement-sync="pending"]')).toHaveCount(2);
  await page
    .getByRole('button', { name: 'Fixture close or reopen card' })
    .click();
  await expect(page.locator('[data-placement-sync="pending"]')).toHaveCount(1);
  await page
    .getByRole('button', { name: 'Fixture close or reopen card' })
    .click();
  await expect(page.locator('[data-placement-sync="pending"]')).toHaveCount(2);
  const session = await browser.newContext();
  try {
    const other = await session.newPage();
    await source(other, () => mode);
    await other.goto(new URL('/__s5.html', page.url()).toString());
    await expect(other.locator('[data-placement-sync="pending"]')).toHaveCount(
      2
    );
    mode = 'off';
    await page.getByRole('button', { name: 'Fixture next reader' }).click();
    await expect(page.locator('[data-placement-sync]')).toHaveCount(0);
    await expect(page.getByText(row.name, { exact: true })).toBeVisible();
  } finally {
    await session.close();
  }
});

test('phone projection labels retain normal-text contrast and stay inside the viewport', async ({
  page
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await source(page, () => 'pending');
  await page.goto('/__s5.html?scheme=light');
  const badges = page.locator('[data-placement-sync="pending"]');
  await expect(badges).toHaveCount(2);
  const measurements = await badges.evaluateAll((elements) =>
    elements.map((element) => {
      const rgb = (value: string) =>
        value
          .match(/[0-9.]+/g)!
          .slice(0, 3)
          .map(Number);
      const luminance = (values: number[]) =>
        values
          .map((v) => v / 255)
          .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
          .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
      let parent: Element | null = element;
      let background = 'rgb(255, 255, 255)';
      while (parent) {
        const color = getComputedStyle(parent).backgroundColor;
        if (color !== 'transparent' && !/^rgba\([^)]*,\s*0\)$/.test(color)) {
          background = color;
          break;
        }
        parent = parent.parentElement;
      }
      const foreground = luminance(rgb(getComputedStyle(element).color));
      const back = luminance(rgb(background));
      const bounds = element.getBoundingClientRect();
      return {
        contrast:
          (Math.max(foreground, back) + 0.05) /
          (Math.min(foreground, back) + 0.05),
        left: bounds.left,
        right: bounds.right,
        width: innerWidth
      };
    })
  );
  for (const measurement of measurements) {
    expect(measurement.contrast).toBeGreaterThanOrEqual(4.5);
    expect(measurement.left).toBeGreaterThanOrEqual(0);
    expect(measurement.right).toBeLessThanOrEqual(measurement.width);
  }
  await page.screenshot({
    path: `${process.env.S5_UI_STATE}/phone-pending.png`,
    fullPage: true
  });
});

test('late reads from a previous identity cannot restore its current badge', async ({
  page
}) => {
  let generation = 1;
  let block = false;
  let completed = 0;
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/assets/locations/**', async (route) => {
    if (route.request().url().includes('/context/')) {
      await route.fulfill({
        json: { workspaces: [{ pk: 3, name: 'Synthetic workspace' }] }
      });
      return;
    }
    const captured = generation;
    const delayed = block && captured === 1;
    if (delayed) await gate;
    const { placement_sync, ...native } = row;
    await route.fulfill({
      json: {
        count: 1,
        next: null,
        results: [
          captured === 1
            ? {
                ...row,
                placement_sync: { ...placement_sync, state: 'current' }
              }
            : native
        ]
      }
    });
    if (delayed) completed += 1;
  });
  await page.goto('/__s5.html');
  await expect(page.locator('[data-placement-sync="current"]')).toHaveCount(2);
  block = true;
  await page
    .getByRole('button', { name: 'Fixture refresh authorized records' })
    .click();
  await expect(page.locator('[data-placement-sync="checking"]')).toHaveCount(2);
  generation = 2;
  await page.getByRole('button', { name: 'Fixture next reader' }).click();
  await expect(page.locator('[data-placement-sync]')).toHaveCount(0);
  await expect(page.getByText(row.name, { exact: true })).toBeVisible();
  release();
  await expect.poll(() => completed).toBe(2);
  await expect(page.locator('[data-placement-sync]')).toHaveCount(0);
});
