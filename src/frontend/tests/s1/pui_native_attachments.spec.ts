import { readFile } from 'node:fs/promises';
import { type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';

nativeBaseline();

const revision = 'a'.repeat(64);
const records = [
  { pk: 901, filename: 'Synthetic nameplate.png', is_image: true },
  { pk: 902, filename: 'Synthetic manual.pdf', is_image: false },
  { pk: 903, filename: 'Synthetic archive.zip', is_image: false },
  { pk: 904, filename: 'Synthetic drawing.svg', is_image: false },
  { pk: 905, filename: 'External reference', is_image: false }
].map((record) => ({
  ...record,
  attachment:
    record.pk === 905
      ? null
      : `/media/attachments/assetmachine/18/${record.filename}`,
  link:
    record.pk === 905 ? 'https://example.invalid/synthetic-manual.pdf' : null,
  thumbnail: null,
  model_type: 'assetmachine',
  model_id: 18,
  comment: 'Synthetic resource; applicability not verified',
  upload_date: '2026-10-18T12:00:00Z',
  upload_user: 11,
  user_detail: { pk: 11, username: 'Synthetic uploader' },
  file_size: 1024,
  tags: []
}));

async function mockResources(page: Page) {
  await mockMachine(page, { canChange: false });
  await page.route('**/api/notifications/?*', (route) =>
    route.fulfill({ json: { count: 0, results: [], next: null } })
  );
  await page.route(/\/api\/ai\/threads(?:\?|$)/, (route) =>
    route.fulfill({ json: { threads: [], shared_threads: [] } })
  );
  await page.route('**/api/ai/voice/capability', (route) =>
    route.fulfill({ json: { enabled: false, voices: [] } })
  );
  await page.route('**/api/aichat/ui/capabilities/', (route) =>
    route.fulfill({
      json: {
        version: 1,
        risk_radar: false,
        command_center: false,
        maintenance_metrics: 1
      }
    })
  );
  const requests: {
    method: string;
    search: string;
    model: string | null;
    owner: string | null;
  }[] = [];
  await page.route('**/api/attachment/**', (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    requests.push({
      method,
      search: url.searchParams.get('search') ?? '',
      model: url.searchParams.get('model_type'),
      owner: url.searchParams.get('model_id')
    });
    if (method === 'OPTIONS') return route.fulfill({ json: { actions: {} } });
    const filtered = records.filter((record) =>
      record.filename
        .toLowerCase()
        .includes((url.searchParams.get('search') ?? '').toLowerCase())
    );
    return route.fulfill({
      json: { count: filtered.length, results: filtered, next: null }
    });
  });
  await page.route('**/api/aichat/evidence/media/*/metadata/**', (route) =>
    route.fulfill({
      json: {
        revision,
        content_type: route.request().url().includes('/902/')
          ? 'application/pdf'
          : 'image/png',
        page_count: route.request().url().includes('/902/') ? 2 : null,
        page_labels: route.request().url().includes('/902/') ? ['1', '2'] : []
      }
    })
  );
  const bytes: string[] = [];
  await page.route('**/api/aichat/evidence/media/901/?*', async (route) => {
    bytes.push(route.request().url());
    await route.fulfill({
      contentType: 'image/png',
      body: await readFile('tests/fixtures/playwright_custom_logo.png')
    });
  });
  await page.route('**/api/aichat/evidence/media/902/?*', (route) => {
    bytes.push(route.request().url());
    return route.fulfill({
      contentType: 'application/pdf',
      body: '%PDF-1.4\n%%EOF'
    });
  });
  return { requests, bytes };
}

async function openResources(page: Page, role = 'viewer', scheme = 'light') {
  await page.goto(
    `/playwright/s0-native.html?surface=machine-shell&sprint=s1&role=${role}&scheme=${scheme}&entry=/machines/machine/18/attachments/`
  );
  await expect(
    page.getByRole('link', { name: 'Synthetic nameplate.png', exact: true })
  ).toBeVisible();
}

test('machine attachment preview reuses the authorized viewer and restores its trigger', async ({
  page
}, testInfo) => {
  const fixture = await mockResources(page);
  await openResources(page);
  await expect(page.locator('.mantine-datatable-empty-state')).toHaveCSS(
    'opacity',
    '0'
  );
  const trigger = page.getByRole('button', {
    name: 'Preview Synthetic nameplate.png',
    exact: true
  });
  await trigger.focus();
  await trigger.press('Enter');
  const viewer = page.getByTestId('media-evidence-modal');
  await expect(viewer.getByRole('dialog')).toBeVisible();
  await expect(
    viewer.getByRole('img', { name: 'Synthetic nameplate.png', exact: true })
  ).toBeVisible();
  await expect(
    viewer.getByRole('link', { name: 'Open original', exact: true })
  ).toHaveAttribute(
    'href',
    new URL(`/api/aichat/evidence/media/901/?revision=${revision}`, page.url())
      .href
  );
  await expect(page.locator('[role="dialog"][aria-modal="true"]')).toHaveCount(
    1
  );
  await expect
    .poll(() =>
      page
        .locator('#root')
        .evaluate((element) => element instanceof HTMLElement && element.inert)
    )
    .toBe(true);
  const capture = testInfo.outputPath('machine-attachment-preview.png');
  await page.screenshot({
    path: capture,
    fullPage: true,
    animations: 'disabled'
  });
  await testInfo.attach('machine-attachment-preview', {
    path: capture,
    contentType: 'image/png'
  });
  await page.keyboard.press('Escape');
  await expect(viewer).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(
    page.getByRole('tab', { name: 'Attachments', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  expect(fixture.bytes).toHaveLength(1);
  expect(
    fixture.requests.filter(
      (request) => request.method !== 'GET' && request.method !== 'OPTIONS'
    )
  ).toEqual([]);
  expect(
    fixture.requests
      .filter((request) => request.method === 'GET')
      .every(
        (request) => request.model === 'assetmachine' && request.owner === '18'
      )
  ).toBe(true);
});

test('preview preserves native search selection scroll and dark-mode return', async ({
  page
}, testInfo) => {
  const fixture = await mockResources(page);
  await openResources(page, 'editor', 'dark');
  await expect(page.getByText('Superuser Mode', { exact: true })).toHaveCount(
    0
  );
  const search = page.getByRole('textbox', {
    name: 'table-search-input',
    exact: true
  });
  await search.fill('nameplate');
  await expect
    .poll(() =>
      fixture.requests.some((request) => request.search === 'nameplate')
    )
    .toBe(true);
  const row = page.getByRole('row').filter({
    has: page.getByRole('link', {
      name: 'Synthetic nameplate.png',
      exact: true
    })
  });
  await row.getByRole('checkbox').check();
  const trigger = row.getByRole('button', {
    name: 'Preview Synthetic nameplate.png',
    exact: true
  });
  await trigger.scrollIntoViewIfNeeded();
  await trigger.evaluate((element) => {
    element.addEventListener(
      'click',
      () => element.setAttribute('data-preview-scroll', String(window.scrollY)),
      { capture: true, once: true }
    );
  });
  const readCount = fixture.requests.length;
  await trigger.click();
  await expect(trigger).toHaveAttribute('data-preview-scroll', /^\d+$/);
  const position = Number(await trigger.getAttribute('data-preview-scroll'));
  await expect(
    page.getByTestId('media-evidence-modal').getByRole('dialog')
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await expect(search).toHaveValue('nameplate');
  await expect(row.getByRole('checkbox')).toBeChecked();
  expect(fixture.requests).toHaveLength(readCount);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(position);
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth
  }));
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport);
  await testInfo.attach('native-resource-return', {
    body: JSON.stringify({ synthetic: true, position, ...dimensions }),
    contentType: 'application/json'
  });
  const capture = testInfo.outputPath('machine-attachment-return-dark.png');
  await page.screenshot({
    path: capture,
    fullPage: true,
    animations: 'disabled'
  });
  await testInfo.attach('machine-attachment-return-dark', {
    path: capture,
    contentType: 'image/png'
  });
});

test('source-level PDF preview uses the authorized revision without inventing an exact page', async ({
  page
}) => {
  await mockResources(page);
  await openResources(page);
  await page
    .getByRole('button', { name: 'Preview Synthetic manual.pdf', exact: true })
    .click();
  const viewer = page.getByTestId('media-evidence-modal');
  await expect(viewer).toContainText('Page 1 of 2');
  await expect(viewer).not.toContainText('Exact page unavailable');
  await expect(viewer.locator('object')).toHaveAttribute(
    'data',
    new URL(
      `/api/aichat/evidence/media/902/?revision=${revision}#page=1&zoom=100`,
      page.url()
    ).href
  );
  await viewer.getByRole('button', { name: 'Next page', exact: true }).click();
  await expect(viewer).toContainText('Page 2 of 2');
  await expect(viewer.locator('object')).toHaveAttribute(
    'data',
    /#page=2&zoom=100$/
  );
});

for (const status of [403, 404]) {
  test(`unavailable or denied source ${status} keeps navigation and fetches no bytes`, async ({
    page
  }) => {
    const fixture = await mockResources(page);
    await page.route('**/api/aichat/evidence/media/901/metadata/**', (route) =>
      route.fulfill({ status, json: {} })
    );
    await openResources(page);
    const trigger = page.getByRole('button', {
      name: 'Preview Synthetic nameplate.png',
      exact: true
    });
    await trigger.click();
    await expect(page.getByTestId('media-evidence-unavailable')).toBeVisible();
    await expect(
      page.getByRole('link', { name: 'Open original', exact: true })
    ).toHaveCount(0);
    expect(fixture.bytes).toEqual([]);
    await page.keyboard.press('Escape');
    await expect(trigger).toBeFocused();
    await expect(
      page.getByRole('link', { name: 'Synthetic nameplate.png', exact: true })
    ).toBeVisible();
  });
}

test('reopening rechecks source access instead of reusing a successful receipt', async ({
  page
}) => {
  const fixture = await mockResources(page);
  await openResources(page);
  const trigger = page.getByRole('button', {
    name: 'Preview Synthetic nameplate.png',
    exact: true
  });
  await trigger.click();
  await expect(
    page.getByRole('link', { name: 'Open original', exact: true })
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  const readCount = fixture.bytes.length;
  await page.route('**/api/aichat/evidence/media/901/metadata/**', (route) =>
    route.fulfill({ status: 404, json: {} })
  );
  await trigger.click();
  await expect(page.getByTestId('media-evidence-unavailable')).toBeVisible();
  expect(fixture.bytes).toHaveLength(readCount);
});

test('unsupported files external links and viewer permissions retain native behavior', async ({
  page
}) => {
  await mockResources(page);
  await openResources(page);
  for (const filename of ['Synthetic archive.zip', 'Synthetic drawing.svg']) {
    await expect(
      page.getByRole('link', { name: filename, exact: true })
    ).toHaveAttribute('target', '_blank');
    await expect(
      page.getByRole('button', { name: `Preview ${filename}`, exact: true })
    ).toHaveCount(0);
  }
  await expect(
    page.getByRole('link', {
      name: 'https://example.invalid/synthetic-manual.pdf',
      exact: true
    })
  ).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(
    page.getByRole('button', {
      name: 'Preview External reference',
      exact: true
    })
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: /^row-action-menu/ })
  ).toHaveCount(0);
  await expect(page.getByRole('checkbox')).toHaveCount(0);
});

test('editors retain native upload external-link edit and delete controls', async ({
  page
}) => {
  const fixture = await mockResources(page);
  await openResources(page, 'editor');
  await expect(
    page.getByRole('button', {
      name: 'action-button-add-attachment',
      exact: true
    })
  ).toBeVisible();
  await expect(
    page.getByRole('button', {
      name: 'action-button-add-external-link',
      exact: true
    })
  ).toBeVisible();
  const row = page.getByRole('row').filter({
    has: page.getByRole('link', {
      name: 'Synthetic nameplate.png',
      exact: true
    })
  });
  await row.getByRole('button', { name: /^row-action-menu/ }).click();
  await expect(
    page.getByRole('menuitem', { name: 'Edit', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('menuitem', { name: 'Delete', exact: true })
  ).toBeVisible();
  await page.keyboard.press('Escape');
  expect(
    fixture.requests.filter(
      (request) => !['GET', 'OPTIONS'].includes(request.method)
    )
  ).toEqual([]);
});

test('canceling a pending preview restores focus without later media reads', async ({
  page
}) => {
  const fixture = await mockResources(page);
  let releaseMetadata: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    releaseMetadata = resolve;
  });
  await page.route(
    '**/api/aichat/evidence/media/901/metadata/**',
    async (route) => {
      await gate;
      await route.fulfill({
        json: {
          revision,
          content_type: 'image/png',
          page_count: null,
          page_labels: []
        }
      });
    }
  );
  await openResources(page);
  const trigger = page.getByRole('button', {
    name: 'Preview Synthetic nameplate.png',
    exact: true
  });
  try {
    await trigger.click();
    await expect(
      page.getByLabel('Loading source', { exact: true })
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(trigger).toBeFocused();
  } finally {
    releaseMetadata?.();
  }
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(fixture.bytes).toEqual([]);
});
