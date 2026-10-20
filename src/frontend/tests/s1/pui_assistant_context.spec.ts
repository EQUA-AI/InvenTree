import { type Page, expect, test } from '@playwright/test';

import { prepareDrawerFixture } from '../pages/ai_drawer_harness';
import {
  type ObservedRequest,
  observeRequest,
  sseBody,
  threadId
} from '../pages/aichat_harness';
import { nativeBaseline } from '../s0/guards';

nativeBaseline();

async function contextFixture(page: Page) {
  const fixture = await prepareDrawerFixture(page);
  const streamRequests: ObservedRequest[] = [];
  await page.route('**/api/ai/chat/stream', async (route) => {
    streamRequests.push(await observeRequest(route.request()));
    await route.fulfill({
      contentType: 'text/event-stream',
      body: sseBody()
    });
  });
  return { ...fixture, streamRequests };
}

async function openContext(page: Page) {
  await page.goto('/playwright/aichat-drawer.html?actor=reader');
  await page.getByTestId('fixture-ask-machine').click();
  return page.getByRole('textbox', { name: 'Message', exact: true });
}

async function expectBlocked(page: Page, requests: unknown[], message: string) {
  await expect(
    page.getByRole('alert').filter({ hasText: 'Machine context not confirmed' })
  ).toBeVisible();
  await expect(
    page.getByRole('textbox', { name: 'Message', exact: true })
  ).toHaveValue(message);
  await expect(page.getByTestId('ai-chat-routing-hint')).toBeVisible();
  expect(requests).toHaveLength(0);
}

function acknowledgement() {
  return {
    thread_id: threadId,
    version: 1,
    hash: 'a'.repeat(64),
    editable: true,
    display_label: 'Pump A',
    scope: {
      schema_version: 1,
      mode: 'explicit_assets',
      machine_ids: [7],
      date_window: { from: null, to: null },
      source_classes: [
        'controlled_document',
        'asset_attachment',
        'work_order',
        'maintenance_record'
      ],
      display_label: 'Pump A'
    }
  };
}

test('a refused machine context sends nothing and preserves the typed draft for retry', async ({
  page
}, testInfo) => {
  const { streamRequests } = await contextFixture(page);
  let refused = true;
  await page.route('**/api/ai/upload', async (route) => {
    await route.fulfill({
      json: {
        file_id: 'inspection-file',
        filename: 'inspection-note.csv',
        size: 20,
        content_type: 'text/csv',
        thread_id: threadId
      }
    });
  });
  await page.route('**/api/ai/threads/*/scope', async (route) => {
    if (route.request().method() !== 'PUT' || !refused) return route.fallback();
    await route.fulfill({
      status: 403,
      json: { detail: 'scope_update_rejected' }
    });
  });
  const composer = await openContext(page);
  await composer.fill('Keep this exact inspection question.');
  const chooser = page.waitForEvent('filechooser');
  await page.getByTestId('attach-ai-chat-file').click();
  await (await chooser).setFiles({
    name: 'inspection-note.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('value,unit\n0,mm/s\n')
  });
  await expect(
    page.getByText('inspection-note.csv', { exact: true })
  ).toBeVisible();
  await page.getByTestId('send-ai-chat-message').click();
  await expectBlocked(
    page,
    streamRequests,
    'Keep this exact inspection question.'
  );
  await expect(
    page.getByText('inspection-note.csv', { exact: true })
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('assistant-context-refused.png'),
    animations: 'disabled'
  });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.screenshot({
    path: testInfo.outputPath('assistant-context-refused-dark.png'),
    animations: 'disabled'
  });
  const originalViewport = page.viewportSize();
  for (const width of [1440, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    await expectBlocked(
      page,
      streamRequests,
      'Keep this exact inspection question.'
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: testInfo.outputPath(`assistant-context-refused-${width}.png`),
      animations: 'disabled'
    });
  }
  if (originalViewport) await page.setViewportSize(originalViewport);
  refused = false;
  await page.getByTestId('send-ai-chat-message').click();
  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();
  expect(streamRequests).toHaveLength(1);
  expect(streamRequests[0].body?.message).toBe(
    'Keep this exact inspection question.'
  );
  expect(streamRequests[0].body?.file_ids).toEqual(['inspection-file']);
  expect(streamRequests[0].body?.expected_scope_version).toBe(1);
  await expect(composer).toHaveValue('');
  await expect(
    page.getByText('inspection-note.csv', { exact: true })
  ).toHaveCount(0);
  await expect(page.getByTestId('ai-chat-routing-hint')).toHaveCount(0);
});

for (const status of [404, 503]) {
  test(`scope ${status} cannot silently become an unscoped send`, async ({
    page
  }) => {
    const { streamRequests } = await contextFixture(page);
    await page.route('**/api/ai/threads/*/scope', async (route) => {
      if (route.request().method() !== 'PUT') return route.fallback();
      await route.fulfill({ status, json: { detail: 'Unavailable' } });
    });
    const composer = await openContext(page);
    await composer.fill('Keep the bounded question.');
    await page.getByTestId('send-ai-chat-message').click();
    await expectBlocked(page, streamRequests, 'Keep the bounded question.');
  });
}

test('missing capability blocks the launch until the user explicitly removes the machine selection', async ({
  page
}) => {
  const { foundation, streamRequests } = await contextFixture(page);
  await page.route(/\/api\/ai\/threads(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: {
        threads: [
          {
            thread_id: threadId,
            title: 'Server conversation',
            summary: '',
            created_at: '2026-10-19T00:00:00Z',
            last_activity: '2026-10-19T00:00:00Z'
          }
        ],
        shared_threads: [],
        capabilities: { thread_scope: false },
        sync_token: null,
        has_more: false
      }
    })
  );
  const composer = await openContext(page);
  await composer.fill('Hello');
  await page.getByTestId('send-ai-chat-message').click();
  await expectBlocked(page, streamRequests, 'Hello');
  expect(foundation.scopeMutations).toHaveLength(0);
  await page.getByTestId('dismiss-routing-hint').click();
  await expect(
    page.getByRole('alert').filter({ hasText: 'Machine context not confirmed' })
  ).toHaveCount(0);
  await page.getByTestId('send-ai-chat-message').click();
  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();
  expect(streamRequests).toHaveLength(1);
  expect(streamRequests[0].body?.message).toBe('Hello');
  expect(foundation.scopeMutations).toHaveLength(0);
});

for (const mismatch of [
  'machine',
  'thread',
  'version',
  'schema',
  'hash',
  'fleet'
]) {
  test(`a successful HTTP response with mismatched ${mismatch} is not a scope acknowledgement`, async ({
    page
  }) => {
    const { streamRequests } = await contextFixture(page);
    await page.route('**/api/ai/threads/*/scope', async (route) => {
      if (route.request().method() !== 'PUT') return route.fallback();
      const response = acknowledgement();
      if (mismatch === 'machine') response.scope.machine_ids = [8];
      if (mismatch === 'thread') response.thread_id = 'another-thread';
      if (mismatch === 'version') response.version = 9;
      if (mismatch === 'schema') response.scope.schema_version = 2;
      if (mismatch === 'hash') response.hash = '';
      if (mismatch === 'fleet') {
        response.scope.mode = 'all_authorized_assets';
        response.scope.machine_ids = [];
      }
      await route.fulfill({ json: response });
    });
    const composer = await openContext(page);
    await composer.fill('Retain exact context.');
    await page.getByTestId('send-ai-chat-message').click();
    await expectBlocked(page, streamRequests, 'Retain exact context.');
  });
}

test('a native version conflict refreshes and acknowledges the exact scope once before sending', async ({
  page
}) => {
  const { foundation, streamRequests } = await contextFixture(page);
  let attempts = 0;
  await page.route('**/api/ai/threads/*/scope', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    attempts += 1;
    if (attempts > 1) return route.fallback();
    foundation.scope.version = 3;
    foundation.scope.mode = 'all_authorized_assets';
    await route.fulfill({
      status: 409,
      json: { detail: 'scope_version_conflict' }
    });
  });
  const composer = await openContext(page);
  await composer.fill('Use the acknowledged scope.');
  await page.getByTestId('send-ai-chat-message').click();
  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();
  expect(attempts).toBe(2);
  expect(foundation.scopeMutations[0].body?.expected_version).toBe(3);
  expect(streamRequests).toHaveLength(1);
  expect(streamRequests[0].body?.expected_scope_version).toBe(4);
});

test('an unavailable conflict refresh does not guess version zero or resend', async ({
  page
}) => {
  const { streamRequests } = await contextFixture(page);
  let conflicted = false;
  let attempts = 0;
  await page.route('**/api/ai/threads/*/scope', async (route) => {
    if (route.request().method() === 'PUT') {
      conflicted = true;
      attempts += 1;
      return route.fulfill({
        status: 409,
        json: { detail: 'scope_version_conflict' }
      });
    }
    if (conflicted)
      return route.fulfill({ status: 404, json: { detail: 'Unavailable' } });
    return route.fallback();
  });
  const composer = await openContext(page);
  await composer.fill('Do not guess the current version.');
  await page.getByTestId('send-ai-chat-message').click();
  await expectBlocked(
    page,
    streamRequests,
    'Do not guess the current version.'
  );
  expect(attempts).toBe(1);
});

test('repeated activation during acknowledgement produces only one scope mutation and one send', async ({
  page
}) => {
  const { streamRequests } = await contextFixture(page);
  let releaseScope: () => void = () => {};
  const pending = new Promise<void>((resolve) => {
    releaseScope = resolve;
  });
  let attempts = 0;
  await page.route('**/api/ai/threads/*/scope', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    attempts += 1;
    await pending;
    return route.fallback();
  });
  const composer = await openContext(page);
  await composer.fill('Only one send.');
  try {
    await page
      .getByTestId('send-ai-chat-message')
      .evaluate((element: HTMLButtonElement) => {
        element.click();
        element.click();
      });
    await expect.poll(() => attempts).toBe(1);
    await expect(composer).toBeDisabled();
    await expect(page.getByTestId('attach-ai-chat-file')).toBeDisabled();
    expect(streamRequests).toHaveLength(0);
  } finally {
    releaseScope();
  }
  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();
  expect(attempts).toBe(1);
  expect(streamRequests).toHaveLength(1);
});

test('closing during acknowledgement cancels the old send without clearing the retained draft', async ({
  page
}) => {
  const { streamRequests } = await contextFixture(page);
  let releaseScope: () => void = () => {};
  const pending = new Promise<void>((resolve) => {
    releaseScope = resolve;
  });
  let started = false;
  await page.route('**/api/ai/threads/*/scope', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    started = true;
    await pending;
    return route.fallback();
  });
  const composer = await openContext(page);
  await composer.fill('Do not send after closing.');
  await page.getByTestId('send-ai-chat-message').click();
  try {
    await expect.poll(() => started).toBe(true);
    await page.getByTestId('close-ai-chat').click();
  } finally {
    releaseScope();
  }
  await page.getByTestId('open-ai-chat').click();
  await expect(composer).toHaveValue('Do not send after closing.');
  await expect(page.getByTestId('ai-chat-routing-hint')).toHaveCount(0);
  expect(streamRequests).toHaveLength(0);
});

test('a late canonical scope read cannot overwrite a newer acknowledged scope', async ({
  page
}) => {
  const { foundation, streamRequests } = await contextFixture(page);
  foundation.scope.mode = 'explicit_assets';
  foundation.scope.version = 2;
  foundation.scope.machineIds = [8];
  foundation.scope.displayLabel = 'Pump A';
  let releaseScope: () => void = () => {};
  const pending = new Promise<void>((resolve) => {
    releaseScope = resolve;
  });
  let started = false;
  await page.route('**/api/ai/threads/*/scope', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    started = true;
    const stale = acknowledgement();
    stale.version = 2;
    stale.scope.machine_ids = [8];
    await pending;
    await route.fulfill({ json: stale });
  });
  const scopeResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/scope') && response.request().method() === 'GET'
  );
  const composer = await openContext(page);
  try {
    await expect.poll(() => started).toBe(true);
    await composer.fill('Confirm machine seven first.');
    await page.getByTestId('send-ai-chat-message').click();
    await expect(
      page.getByText('Golden typed response', { exact: true })
    ).toBeVisible();
    expect(streamRequests[0].body?.expected_scope_version).toBe(3);
  } finally {
    releaseScope();
  }
  await scopeResponse;
  await composer.fill('Keep the confirmed scope for the next message.');
  await page.getByTestId('send-ai-chat-message').click();
  await expect.poll(() => streamRequests.length).toBe(2);
  expect(streamRequests[1].body?.expected_scope_version).toBe(3);
});

test('a matching display name never hides a different canonical machine identity', async ({
  page
}) => {
  const { foundation, streamRequests } = await contextFixture(page);
  foundation.scope.mode = 'explicit_assets';
  foundation.scope.version = 2;
  foundation.scope.machineIds = [8];
  foundation.scope.displayLabel = 'Pump A';
  const composer = await openContext(page);
  await expect(page.getByTestId('ai-chat-analysis-context')).toContainText(
    'Analyzing: Pump A'
  );
  await expect(page.getByTestId('ai-chat-routing-hint')).toBeVisible();
  await composer.fill('Use machine seven, not its namesake.');
  await page.getByTestId('send-ai-chat-message').click();
  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();
  expect(foundation.scopeMutations[0].body?.scope).toMatchObject({
    machine_ids: [7]
  });
  expect(streamRequests[0].body?.expected_scope_version).toBe(3);
});
