/** C03/T09–T12/T25: mounted drawer, wholly mocked auth and upload/chat APIs. */
import { type Page, expect, test } from '@playwright/test';
import {
  loadDrawerFixture,
  prepareDrawerFixture
} from './ai_drawer_harness.js';
import { goldenEvents, sseBody, threadId } from './aichat_harness.js';

const editor = (page: Page) =>
  page.getByRole('textbox', { name: 'Message', exact: true });
const send = (page: Page) => page.getByTestId('send-ai-chat-message');
const file = (name: string) => ({
  name,
  mimeType: 'text/plain',
  buffer: Buffer.from(`Mock attachment ${name}`)
});
const row = (page: Page, name: string) =>
  page.getByTestId('ai-chat-attachment').filter({ hasText: name });
const secondId = 'thread-upload-b';

function gate() {
  let release = () => {};
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function setup(page: Page, withSecondThread = false) {
  const fixture = await prepareDrawerFixture(page, {
    voice: false,
    tab: 'chat'
  });
  const posts: { message: string; thread_id: string; file_ids?: string[] }[] =
    [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/ai/**', async (route) => {
    const request = route.request();
    if (
      request.method() === 'POST' &&
      /\/(chat(?:\/stream)?|agui)\/?$/.test(new URL(request.url()).pathname)
    ) {
      posts.push(request.postDataJSON());
      await route.fulfill({
        contentType: 'text/event-stream',
        body: sseBody(goldenEvents)
      });
      return;
    }
    await route.fallback();
  });
  if (withSecondThread) {
    await page.route('**/api/ai/threads**', async (route) => {
      if (
        route.request().method() !== 'GET' ||
        !new URL(route.request().url()).pathname.endsWith('/threads')
      )
        return route.fallback();
      await route.fulfill({
        json: {
          threads: [threadId, secondId].map((id) => ({
            thread_id: id,
            title:
              id === threadId ? 'Server conversation' : 'Upload conversation B',
            created_at: '2026-10-24T00:00:00Z',
            last_activity: '2026-10-24T00:00:00Z',
            is_persisted: true
          })),
          shared_threads: [],
          has_more: false,
          capabilities: { thread_scope: true, thread_focus: true }
        }
      });
    });
    await page.route(`**/api/ai/threads/${secondId}`, async (route) => {
      await route.fulfill({
        json: {
          thread_id: secondId,
          title: 'Upload conversation B',
          messages: [],
          active_scope: {
            mode: 'legacy_unconfirmed',
            version: 0,
            display_label: ''
          }
        }
      });
    });
  }
  await loadDrawerFixture(page);
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: 'Server conversation' })
    .click();
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  await expect(editor(page)).toBeEnabled();
  return { fixture, posts, errors };
}

function uploaded(name: string, id = name) {
  return {
    file_id: id,
    filename: name,
    content_type: 'text/plain',
    size: 12,
    thread_id: threadId
  };
}

test('C03: a slow upload blocks Enter and Send, then submits the intended ready file with edited text', async ({
  page
}) => {
  const { posts, errors } = await setup(page);
  const upload = gate();
  let started = false;
  await page.route('**/api/ai/upload', async (route) => {
    started = true;
    await upload.promise;
    await route.fulfill({ json: uploaded('slow.txt') });
  });
  try {
    await editor(page).fill('Analyze my attachment');
    await page.locator('input[type=file]').setInputFiles(file('slow.txt'));
    await expect.poll(() => started).toBe(true);
    await editor(page).press('Enter');
    await expect(editor(page)).toHaveValue('Analyze my attachment');
    await expect(send(page)).toBeDisabled();
    await expect(row(page, 'slow.txt')).toContainText('Uploading');
    expect(posts).toEqual([]);
    await editor(page).fill('Analyze my attachment with this newer question');
    upload.release();
    await expect(
      page.getByTestId('remove-ai-chat-attachment-slow.txt')
    ).toBeVisible();
    await expect(send(page)).toBeEnabled();
    await send(page).click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0]).toMatchObject({
      thread_id: threadId,
      message: 'Analyze my attachment with this newer question',
      file_ids: ['slow.txt']
    });
    await expect(editor(page)).toHaveValue('');
    await expect(row(page, 'slow.txt')).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    upload.release();
  }
});

test('C03: mixed upload failures stay visible and block sending until the failed file is retried', async ({
  page
}, testInfo) => {
  const { posts, errors } = await setup(page);
  let failedAttempts = 0;
  await page.route('**/api/ai/upload', async (route) => {
    const failing = route
      .request()
      .postData()
      ?.includes('filename="retry.txt"');
    if (failing && ++failedAttempts === 1) {
      await route.fulfill({
        status: 503,
        json: { detail: 'Mock upload unavailable' }
      });
      return;
    }
    const name = failing ? 'retry.txt' : 'ready.txt';
    await route.fulfill({ json: uploaded(name) });
  });
  await editor(page).fill('Use both selected files');
  await page
    .locator('input[type=file]')
    .setInputFiles([file('ready.txt'), file('retry.txt')]);
  await expect(
    page.getByTestId('remove-ai-chat-attachment-ready.txt')
  ).toBeVisible();
  await expect(row(page, 'retry.txt')).toContainText('Upload failed');
  await page.screenshot({ path: testInfo.outputPath('desktop-queue.png') });
  await editor(page).press('Enter');
  await expect(send(page)).toBeDisabled();
  expect(posts).toEqual([]);
  await row(page, 'retry.txt')
    .getByRole('button', { name: 'Retry', exact: true })
    .click();
  await expect(
    page.getByTestId('remove-ai-chat-attachment-retry.txt')
  ).toBeVisible();
  await send(page).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].file_ids).toEqual(['ready.txt', 'retry.txt']);
  expect(failedAttempts).toBe(2);
  expect(errors).toEqual([]);
});

test('C03: removing an uploading file rejects its late completion and preserves a ready-file control', async ({
  page
}) => {
  const { posts } = await setup(page);
  const upload = gate();
  let slowStarted = false;
  let slowFinished = false;
  await page.route('**/api/ai/upload', async (route) => {
    if (route.request().postData()?.includes('filename="removed.txt"')) {
      slowStarted = true;
      await upload.promise;
      await route.fulfill({ json: uploaded('removed.txt') });
      slowFinished = true;
    } else {
      await route.fulfill({ json: uploaded('keep.txt') });
    }
  });
  try {
    await editor(page).fill('Only the retained file');
    await page
      .locator('input[type=file]')
      .setInputFiles([file('keep.txt'), file('removed.txt')]);
    await expect.poll(() => slowStarted).toBe(true);
    await expect(
      page.getByTestId('remove-ai-chat-attachment-keep.txt')
    ).toBeVisible();
    await row(page, 'removed.txt')
      .getByRole('button', { name: 'Remove removed.txt', exact: true })
      .click();
    upload.release();
    await expect.poll(() => slowFinished).toBe(true);
    await send(page).click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0].file_ids).toEqual(['keep.txt']);
    await expect(row(page, 'removed.txt')).toHaveCount(0);
  } finally {
    upload.release();
  }
});

test('C03: an upload completing while another thread is selected updates only its originating draft', async ({
  page
}) => {
  const { posts } = await setup(page, true);
  const upload = gate();
  let started = false;
  let finished = false;
  await page.route('**/api/ai/upload', async (route) => {
    expect(route.request().postData()).toContain(
      `name="thread_id"\r\n\r\n${threadId}`
    );
    started = true;
    await upload.promise;
    await route.fulfill({ json: uploaded('origin.txt') });
    finished = true;
  });
  try {
    await editor(page).fill('Draft A owns this upload');
    await page.locator('input[type=file]').setInputFiles(file('origin.txt'));
    await expect.poll(() => started).toBe(true);
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    await page
      .getByTestId('ai-chat-history-row')
      .filter({ hasText: 'Upload conversation B' })
      .click();
    await expect(editor(page)).toHaveValue('');
    await editor(page).fill('Draft B remains separate');
    upload.release();
    await expect.poll(() => finished).toBe(true);
    await expect(row(page, 'origin.txt')).toHaveCount(0);
    await expect(editor(page)).toHaveValue('Draft B remains separate');
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    await page
      .getByTestId('ai-chat-history-row')
      .filter({ hasText: 'Server conversation' })
      .click();
    await expect(editor(page)).toHaveValue('Draft A owns this upload');
    await expect(
      page.getByTestId('remove-ai-chat-attachment-origin.txt')
    ).toBeVisible();
    await send(page).click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0]).toMatchObject({
      thread_id: threadId,
      file_ids: ['origin.txt']
    });
  } finally {
    upload.release();
  }
});

test('C03: changing attachments during held preflight retains the newer revision and cancels the prepared send', async ({
  page
}) => {
  const { fixture, posts } = await setup(page);
  fixture.foundation.focus.rows['kanban_card:11'] = {
    kind: 'kanban_card',
    id: 11,
    label: 'Upload target',
    machine_id: 7,
    work_order_id: 5
  };
  const focus = gate();
  let started = false;
  let finished = false;
  await page.route('**/api/ai/threads/*/focus', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    started = true;
    await focus.promise;
    await route.fallback();
    finished = true;
  });
  await page.route('**/api/ai/upload', async (route) => {
    await route.fulfill({ json: uploaded('newer.txt') });
  });
  try {
    await page.getByTestId('fixture-ask-task').click();
    await editor(page).fill('Keep this question');
    await send(page).click();
    await expect.poll(() => started).toBe(true);
    await page.locator('input[type=file]').setInputFiles(file('newer.txt'));
    await expect(
      page.getByTestId('remove-ai-chat-attachment-newer.txt')
    ).toBeVisible();
    focus.release();
    await expect.poll(() => finished).toBe(true);
    await expect(send(page)).toBeEnabled();
    await expect(editor(page)).toHaveValue('Keep this question');
    expect(posts).toEqual([]);
    await send(page).click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0].file_ids).toEqual(['newer.txt']);
  } finally {
    focus.release();
  }
});

test('C03 mobile: a failed selection can be removed without dropping its ready sibling', async ({
  page
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: 'dark' });
  const { posts, errors } = await setup(page);
  const longName = 'maintenance-manual-for-the-primary-pumping-station.txt';
  await page.route('**/api/ai/upload', async (route) => {
    if (route.request().postData()?.includes(`filename="${longName}"`)) {
      await route.fulfill({
        status: 503,
        json: { detail: 'Mock unavailable' }
      });
    } else {
      await route.fulfill({ json: uploaded('keep.txt') });
    }
  });
  await editor(page).fill('Use the remaining file');
  await page
    .locator('input[type=file]')
    .setInputFiles([file('keep.txt'), file(longName)]);
  await expect(row(page, longName)).toContainText('Upload failed');
  await expect(send(page)).toBeDisabled();
  const dimensions = await row(page, longName).evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    return {
      left: bounds.left,
      right: bounds.right,
      viewport: window.innerWidth
    };
  });
  expect(dimensions.left).toBeGreaterThanOrEqual(0);
  expect(dimensions.right).toBeLessThanOrEqual(dimensions.viewport);
  await page.screenshot({ path: testInfo.outputPath('mobile-dark-queue.png') });
  await row(page, longName)
    .getByRole('button', { name: `Remove ${longName}`, exact: true })
    .click();
  await send(page).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].file_ids).toEqual(['keep.txt']);
  expect(errors).toEqual([]);
});

test('C03 admission: rapid Enter during held preflight creates one focus update and one submitted turn', async ({
  page
}) => {
  const { fixture, posts } = await setup(page);
  const focus = gate();
  let started = false;
  await page.route('**/api/ai/threads/*/focus', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    started = true;
    await focus.promise;
    await route.fallback();
  });
  try {
    await page.getByTestId('fixture-ask-task').click();
    await editor(page).fill('One logical question');
    await editor(page).press('Enter');
    await expect.poll(() => started).toBe(true);
    await editor(page).press('Enter');
    await editor(page).press('Enter');
    await expect(send(page)).toBeDisabled();
    expect(posts).toEqual([]);
    focus.release();
    await expect.poll(() => posts.length).toBe(1);
    await expect(editor(page)).toHaveValue('');
    expect(fixture.foundation.focusMutations).toHaveLength(1);
  } finally {
    focus.release();
  }
});
