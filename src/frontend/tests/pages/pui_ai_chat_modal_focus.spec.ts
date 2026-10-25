/** M01: real drawer busy-modal focus; wholly mocked auth/API, no credentials. */
import { type Page, expect, test } from '@playwright/test';
import {
  loadDrawerFixture,
  prepareDrawerFixture
} from './ai_drawer_harness.js';
import { goldenEvents, sseBody, threadId } from './aichat_harness.js';

const secondId = 'thread-draft-b';
const stamp = '2026-10-23T00:00:00Z';
const fileId = 'draft-ready-file';
const editor = (page: Page) =>
  page.getByRole('textbox', { name: 'Message', exact: true });
const attachment = (page: Page) =>
  page.getByTestId(`remove-ai-chat-attachment-${fileId}`);

async function setup(page: Page) {
  const fixture = await prepareDrawerFixture(page, {
    voice: false,
    tab: 'chat'
  });
  let deleted = false;
  const chatPosts: string[] = [];
  await page.route('**/api/ai/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && !path.endsWith('/upload')) {
      chatPosts.push(path);
      await route.fulfill({
        contentType: 'text/event-stream',
        body: sseBody(goldenEvents)
      });
      return;
    }
    await route.fallback();
  });
  await page.route('**/api/ai/threads**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'GET' && path.endsWith('/threads')) {
      await route.fulfill({
        json: {
          threads: (deleted ? [secondId] : [threadId, secondId]).map((id) => ({
            thread_id: id,
            title:
              id === threadId ? 'C06 Conversation A' : 'C06 Conversation B',
            created_at: stamp,
            last_activity: stamp,
            is_persisted: true
          })),
          shared_threads: [],
          has_more: false,
          sync_token: null,
          capabilities: { thread_scope: true, thread_focus: true }
        }
      });
      return;
    }
    const id = path.split('/').at(-1);
    if (request.method() === 'DELETE' && id === threadId) {
      deleted = true;
      await route.fulfill({ json: { thread_id: id, status: 'deleted' } });
      return;
    }
    if (request.method() === 'GET' && (id === threadId || id === secondId)) {
      await route.fulfill({
        json: {
          thread_id: id,
          title: id === threadId ? 'C06 Conversation A' : 'C06 Conversation B',
          created_at: stamp,
          updated_at: stamp,
          messages: [
            {
              id: `${id}-answer`,
              role: 'assistant',
              content: id === threadId ? 'C06 Answer A' : 'C06 Answer B',
              timestamp: stamp
            }
          ],
          active_scope: {
            mode: 'legacy_unconfirmed',
            version: 0,
            display_label: ''
          }
        }
      });
      return;
    }
    await route.fallback();
  });
  await page.route('**/api/ai/upload', async (route) => {
    expect(route.request().method()).toBe('POST');
    expect(route.request().postData()).toContain(
      `name="thread_id"\r\n\r\n${threadId}`
    );
    await route.fulfill({
      json: {
        file_id: fileId,
        filename: 'C06-ready.txt',
        content_type: 'text/plain',
        size: 11,
        thread_id: threadId
      }
    });
  });
  await loadDrawerFixture(page);
  await select(page, 'A');
  return {
    fixture,
    chatPosts
  };
}

async function select(page: Page, letter: 'A' | 'B') {
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: `C06 Conversation ${letter}` })
    .click();
  await expect(
    page.getByText(`C06 Answer ${letter}`, { exact: true })
  ).toBeVisible();
  await expect(editor(page)).toBeEnabled();
}

async function attachReady(page: Page) {
  await page.locator('input[type=file]').setInputFiles({
    name: 'C06-ready.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('C06 fixture')
  });
  await expect(attachment(page)).toBeVisible();
}

const newControl = (page: Page) =>
  page.getByRole('button', { name: 'New conversation', exact: true });

type ClearPost = { message: string; threadId: string };
async function observeChat(page: Page, heldMessage?: string) {
  const posts: ClearPost[] = [];
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/ai/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (
      request.method() !== 'POST' ||
      !/(chat(?:\/stream)?|agui)\/?$/.test(path)
    ) {
      await route.fallback();
      return;
    }
    const payload = request.postDataJSON() as {
      message: string;
      thread_id: string;
    };
    posts.push({ message: payload.message, threadId: payload.thread_id });
    const index = posts.length;
    if (payload.message === heldMessage) await gate;
    const events = goldenEvents.map((event) => ({
      ...event,
      threadId: payload.thread_id,
      runId: `l1-run-${index}`,
      ...(event.messageId ? { messageId: `l1-message-${index}` } : {}),
      ...(event.type === 'TEXT_MESSAGE_CONTENT'
        ? { delta: `L1 reply ${index}` }
        : {})
    }));
    await route.fulfill({
      contentType: 'text/event-stream',
      body: sseBody(events)
    });
  });
  return { posts, release };
}

async function sendWords(
  page: Page,
  value: string,
  posts: ClearPost[],
  count: number
) {
  await editor(page).fill(value);
  await editor(page).press('Enter');
  await expect.poll(() => posts.length).toBe(count);
  expect(posts[count - 1].message).toBe(value);
}

async function busyDeletion(page: Page) {
  await setup(page);
  let preparations = 0;
  let release = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const destructiveRequests: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'DELETE') {
      destructiveRequests.push(new URL(request.url()).pathname);
    }
  });
  await page.route('**/api/ai/threads/deletion-plan', async (route) => {
    expect(route.request().method()).toBe('POST');
    preparations += 1;
    await held;
    await route.fulfill({
      status: 503,
      json: { detail: 'Controlled preparation refusal' }
    });
  });
  await page.getByRole('tab', { name: 'Chat', exact: true }).click();
  await page
    .getByRole('button', { name: 'Choose a conversation', exact: true })
    .click();
  await page.getByTestId('delete-owned-ai-chat-threads').click();
  const modal = page.getByRole('dialog', {
    name: 'Delete my saved conversations?',
    exact: true
  });
  await expect(modal).toBeVisible();
  await modal
    .getByRole('button', { name: 'Delete my saved conversations', exact: true })
    .click();
  await expect.poll(() => preparations).toBe(1);
  await expect(
    modal.getByRole('button', { name: 'Cancel', exact: true })
  ).toBeDisabled();
  return { modal, release, destructiveRequests };
}

test('M01 busy deletion retains keyboard focus inside the modal', async ({
  page
}) => {
  const { modal, release, destructiveRequests } = await busyDeletion(page);
  try {
    // The real Delete click disables the focused control and every alternative.
    // Focus must recover inside the dialog, not remain stranded on document.body.
    await expect
      .poll(() =>
        modal.evaluate((element) => element.contains(document.activeElement))
      )
      .toBe(true);
    for (const key of ['Tab', 'Tab', 'Tab', 'Shift+Tab', 'Shift+Tab', 'Tab']) {
      await page.keyboard.press(key);
      expect(
        await modal.evaluate((element) =>
          element.contains(document.activeElement)
        )
      ).toBe(true);
    }
    expect(destructiveRequests).toEqual([]);
  } finally {
    release();
  }
});

test('M01 busy deletion still blocks close and New, then recovers after refusal', async ({
  page
}) => {
  const { modal, release, destructiveRequests } = await busyDeletion(page);
  const io = await observeChat(page);
  try {
    await page.keyboard.press('Escape');
    await expect(modal).toBeVisible();
    const backgroundNew = page.locator(
      'button[data-testid="new-ai-chat-thread"]:not([role="menuitem"])'
    );
    let blocked = false;
    try {
      await backgroundNew.click({ timeout: 1500 });
    } catch (error) {
      if (error instanceof Error && error.name === 'TimeoutError')
        blocked = true;
      else throw error;
    }
    expect(blocked).toBe(true);
    const box = await backgroundNew.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await expect(modal).toBeVisible();
    await expect(
      modal.getByRole('button', { name: 'Cancel', exact: true })
    ).toBeDisabled();
    release();
    await expect(modal.getByRole('alert')).toBeVisible();
    await modal.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(modal).toHaveCount(0);
    await newControl(page).click();
    await sendWords(page, 'M01 after deletion refusal', io.posts, 1);
    expect(io.posts[0].threadId).not.toBe(threadId);
    await expect(page.getByText('L1 reply 1', { exact: true })).toBeVisible();
    expect(destructiveRequests).toEqual([]);
  } finally {
    release();
  }
});

test('M01 idle New still clears its draft and ready attachments before a new-thread send', async ({
  page
}) => {
  await setup(page);
  const io = await observeChat(page);
  await editor(page).fill('M01 discarded draft');
  await attachReady(page);
  await newControl(page).click();
  await expect(editor(page)).toHaveValue('');
  await expect(attachment(page)).toHaveCount(0);
  await sendWords(page, 'M01 after idle New', io.posts, 1);
  expect(io.posts[0].threadId).not.toBe(threadId);
  await expect(page.getByText('L1 reply 1', { exact: true })).toBeVisible();
});
