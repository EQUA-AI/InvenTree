import { readFile } from 'node:fs/promises';
import { type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { mockWorkList } from './workListFixtures';

nativeBaseline();

const revision = 'a'.repeat(64);

async function mockWorkAttachments(page: Page) {
  await mockWorkList(page);
  const records = [
    { pk: 901, filename: 'Synthetic work nameplate.png', is_image: true },
    { pk: 902, filename: 'Synthetic work manual.pdf', is_image: false },
    { pk: 903, filename: 'Synthetic work archive.zip', is_image: false },
    { pk: 904, filename: 'External work reference', is_image: false }
  ].map((record) => ({
    ...record,
    attachment:
      record.pk === 904
        ? null
        : `/media/attachments/workorder/73/${record.filename}`,
    link: record.pk === 904 ? 'https://example.invalid/reference' : null,
    model_type: 'workorder',
    model_id: 73,
    comment: 'Synthetic work file; applicability not verified',
    thumbnail: null,
    upload_date: '2026-10-18T12:00:00Z',
    upload_user: 11,
    user_detail: { pk: 11, username: 'Synthetic uploader' },
    file_size: 1024,
    tags: []
  }));
  const state = { countStatus: 200, mediaStatus: 200 };
  const requests: {
    method: string;
    params: URLSearchParams;
    body?: Record<string, unknown>;
  }[] = [];
  const bytes: string[] = [];
  await page.route('**/api/attachment/**', (route) => {
    const params = new URL(route.request().url()).searchParams;
    const method = route.request().method();
    if (method === 'OPTIONS')
      return route.fulfill({
        json: {
          name: 'Attachments',
          actions: {
            POST: {
              link: { type: 'url', required: true, label: 'Link' },
              comment: { type: 'string', required: false, label: 'Comment' },
              model_type: { type: 'string' },
              model_id: { type: 'integer' }
            }
          }
        }
      });
    const body =
      method === 'POST'
        ? (route.request().postDataJSON() as Record<string, unknown>)
        : undefined;
    requests.push({ method, params, body });
    if (method === 'POST') {
      const created = {
        ...records[3],
        pk: 905,
        filename: String(body?.link),
        link: String(body?.link),
        comment: String(body?.comment ?? '')
      };
      records.push(created);
      return route.fulfill({ status: 201, json: created });
    }
    if (params.get('limit') === '1' && state.countStatus !== 200)
      return route.fulfill({
        status: state.countStatus,
        json: { detail: 'Synthetic count denial' }
      });
    const search = (params.get('search') ?? '').toLowerCase();
    const filtered = records.filter((record) =>
      record.filename.toLowerCase().includes(search)
    );
    const limit = Number(params.get('limit') ?? 25);
    return route.fulfill({
      json: {
        count: filtered.length,
        results: filtered.slice(0, limit),
        next: null
      }
    });
  });
  await page.route('**/api/aichat/evidence/media/901/metadata/**', (route) =>
    route.fulfill({
      status: state.mediaStatus,
      json:
        state.mediaStatus === 200
          ? {
              revision,
              content_type: 'image/png',
              page_count: null,
              page_labels: []
            }
          : { detail: 'Source unavailable' }
    })
  );
  await page.route('**/api/aichat/evidence/media/901/?*', async (route) => {
    bytes.push(route.request().url());
    await route.fulfill({
      contentType: 'image/png',
      body: await readFile('tests/fixtures/playwright_custom_logo.png')
    });
  });
  return { records, state, requests, bytes };
}

async function openWorkAttachments(page: Page, role = 'viewer', query = '') {
  const params = new URLSearchParams({
    surface: 'maintenance-shell',
    sprint: 's1',
    role,
    work_access: 'reader',
    entry: `/maintenance/work-orders/73/?work_panel=parts${query}`
  });
  await page.goto(`/playwright/s0-native.html?${params}`);
  await page.getByRole('button', { name: /^Attachments/ }).click();
  await expect(
    page.getByRole('heading', { name: 'Work attachments', exact: true })
  ).toBeVisible();
}

test('a counted native work resource previews the exact owner and returns to the active workflow panel', async ({
  page
}, testInfo) => {
  const fixture = await mockWorkAttachments(page);
  await openWorkAttachments(page);
  await expect(
    page.getByRole('button', { name: 'Attachments (4)', exact: true })
  ).toHaveAttribute('aria-expanded', 'true');
  const tabs = page.getByRole('tablist', {
    name: 'Work workflow',
    exact: true
  });
  await expect(tabs.getByRole('tab')).toHaveCount(7);
  const trigger = page.getByRole('button', {
    name: 'Preview Synthetic work nameplate.png',
    exact: true
  });
  await trigger.focus();
  await trigger.press('Enter');
  const viewer = page.getByTestId('media-evidence-modal');
  await expect(
    viewer.getByRole('img', {
      name: 'Synthetic work nameplate.png',
      exact: true
    })
  ).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(
    viewer.getByRole('link', { name: 'Open related record', exact: true })
  ).toHaveAttribute('href', /\/maintenance\/work-orders\/73\/$/);
  await page.screenshot({
    path: testInfo.outputPath('work-attachment-preview.png'),
    animations: 'disabled'
  });
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await expect(
    tabs.getByRole('tab', { name: 'Parts', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  expect(fixture.bytes).toHaveLength(1);
  expect(
    fixture.requests
      .filter((request) => request.method === 'GET')
      .every(
        (request) =>
          request.params.get('model_type') === 'workorder' &&
          request.params.get('model_id') === '73'
      )
  ).toBe(true);
  expect(
    fixture.requests.filter((request) => request.method !== 'GET')
  ).toHaveLength(0);
});

test('resource close and preview retain native search selection and total owner count', async ({
  page
}) => {
  await mockWorkAttachments(page);
  await openWorkAttachments(page, 'editor');
  const search = page.getByRole('textbox', {
    name: 'table-search-input',
    exact: true
  });
  await search.fill('nameplate');
  const row = page.getByRole('row').filter({
    has: page.getByRole('link', {
      name: 'Synthetic work nameplate.png',
      exact: true
    })
  });
  await row.getByRole('checkbox').check();
  await row
    .getByRole('button', {
      name: 'Preview Synthetic work nameplate.png',
      exact: true
    })
    .click();
  await expect(
    page.getByTestId('media-evidence-modal').getByRole('dialog')
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(search).toHaveValue('nameplate');
  await expect(row.getByRole('checkbox')).toBeChecked();
  await expect(
    page.getByRole('button', { name: 'Attachments (4)', exact: true })
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Close attachments', exact: true })
    .click();
  await expect(search).toBeHidden();
  await page.getByRole('button', { name: /^Attachments/ }).click();
  await expect(search).toHaveValue('nameplate');
  await expect(row.getByRole('checkbox')).toBeChecked();
  await expect(
    page
      .getByRole('tablist', { name: 'Work workflow', exact: true })
      .getByRole('tab', { name: 'Parts', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
});

test('denied work media shows unavailable without bytes and restores the exact native trigger', async ({
  page
}) => {
  const fixture = await mockWorkAttachments(page);
  fixture.state.mediaStatus = 403;
  await openWorkAttachments(page);
  const trigger = page.getByRole('button', {
    name: 'Preview Synthetic work nameplate.png',
    exact: true
  });
  await trigger.click();
  await expect(page.getByTestId('media-evidence-unavailable')).toBeVisible();
  expect(fixture.bytes).toHaveLength(0);
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
});

test('a denied count is not zero or a cached table and retries within the same work owner', async ({
  page
}) => {
  const fixture = await mockWorkAttachments(page);
  fixture.state.countStatus = 403;
  await openWorkAttachments(page);
  await expect(
    page.getByRole('alert').filter({ hasText: 'Attachments unavailable' })
  ).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Attachments (0)', exact: true })
  ).toHaveCount(0);
  fixture.state.countStatus = 200;
  await page
    .getByRole('button', { name: 'Retry attachments', exact: true })
    .click();
  await expect(
    page.getByRole('link', {
      name: 'Synthetic work nameplate.png',
      exact: true
    })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Attachments (4)', exact: true })
  ).toBeVisible();
});

test('a native external-link save keeps work ownership and refreshes the resource count', async ({
  page
}) => {
  const fixture = await mockWorkAttachments(page);
  await openWorkAttachments(page, 'editor');
  await page
    .getByRole('button', {
      name: 'action-button-add-external-link',
      exact: true
    })
    .click();
  const dialog = page.getByRole('dialog').filter({
    has: page.getByRole('heading', { name: 'Upload Attachment', exact: true })
  });
  await dialog
    .getByRole('textbox', { name: 'text-field-link', exact: true })
    .fill('https://example.invalid/saved-work-reference');
  await dialog.getByRole('button', { name: 'Submit', exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(
    page.getByRole('button', { name: 'Attachments (5)', exact: true })
  ).toBeVisible();
  const writes = fixture.requests.filter(
    (request) => request.method === 'POST'
  );
  expect(writes).toHaveLength(1);
  expect(writes[0].body?.model_type).toBe('workorder');
  expect(Number(writes[0].body?.model_id)).toBe(73);
  await expect(
    page
      .getByRole('tablist', { name: 'Work workflow', exact: true })
      .getByRole('tab', { name: 'Parts', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
});

test('page query parameters cannot retarget a bound work resource or disable its native filters', async ({
  page
}, testInfo) => {
  const fixture = await mockWorkAttachments(page);
  await openWorkAttachments(
    page,
    'viewer',
    '&model_type=assetmachine&model_id=999&search=unrelated'
  );
  await expect
    .poll(() =>
      fixture.requests.some(
        (request) =>
          request.method === 'GET' && request.params.get('limit') !== '1'
      )
    )
    .toBe(true);
  expect(
    fixture.requests
      .filter((request) => request.method === 'GET')
      .every(
        (request) =>
          request.params.get('model_type') === 'workorder' &&
          request.params.get('model_id') === '73'
      )
  ).toBe(true);
  await expect(
    page.getByRole('alert', {
      name: 'Custom table filters are active',
      exact: true
    })
  ).toHaveCount(0);
  const search = page.getByRole('textbox', {
    name: 'table-search-input',
    exact: true
  });
  await expect(search).toBeEnabled();
  await expect(
    page.getByRole('button', { name: 'table-select-filters', exact: true })
  ).toBeEnabled();
  await search.fill('nameplate');
  await expect
    .poll(() =>
      fixture.requests.some(
        (request) =>
          request.method === 'GET' &&
          request.params.get('search') === 'nameplate' &&
          request.params.get('model_type') === 'workorder' &&
          request.params.get('model_id') === '73'
      )
    )
    .toBe(true);
  await expect(page.getByRole('table').getByRole('row')).toHaveCount(2);
  await expect(
    page.getByRole('link', {
      name: 'Synthetic work nameplate.png',
      exact: true
    })
  ).toBeVisible();
  await expect(
    page
      .getByRole('tablist', { name: 'Work workflow', exact: true })
      .getByRole('tab', { name: 'Parts', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  await page
    .getByRole('heading', { name: 'Work attachments', exact: true })
    .evaluate((element) => element.scrollIntoView({ block: 'start' }));
  await page.evaluate(() => window.scrollBy(0, -90));
  await page.screenshot({
    path: testInfo.outputPath('work-attachments-bound.png'),
    animations: 'disabled'
  });
  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(search).toHaveValue('nameplate');
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath('work-attachments-tablet.png'),
    animations: 'disabled'
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(search).toHaveValue('nameplate');
  await page.screenshot({
    path: testInfo.outputPath('work-attachments-desktop-wide.png'),
    animations: 'disabled'
  });
});
