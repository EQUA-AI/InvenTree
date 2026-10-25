/** C06/T17/T23: real drawer and hooks, wholly mocked auth/API, no credentials. */
import { type Page, expect, test } from '@playwright/test';
import {
  loadDrawerFixture,
  openChat,
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

test('C06: thread A and B restore their own text drafts without cross-thread leakage', async ({
  page
}) => {
  await setup(page);
  await editor(page).fill('C06 private draft A');
  await select(page, 'B');
  await expect(editor(page)).toHaveValue('');
  await editor(page).fill('C06 private draft B');
  await select(page, 'A');
  await expect(editor(page)).toHaveValue('C06 private draft A');
  await select(page, 'B');
  await expect(editor(page)).toHaveValue('C06 private draft B');
});

test('C06: ready attachment references return only to their originating thread', async ({
  page
}) => {
  await setup(page);
  await attachReady(page);
  await select(page, 'B');
  await expect(attachment(page)).toHaveCount(0);
  await select(page, 'A');
  await expect(attachment(page)).toBeVisible();
  await attachment(page).click();
  await expect(attachment(page)).toHaveCount(0);
  await select(page, 'B');
  await select(page, 'A');
  await expect(attachment(page)).toHaveCount(0);
});

test('C06 positive: close and reopen preserves text and ready references without browser-storage persistence', async ({
  page
}) => {
  await setup(page);
  await editor(page).fill('C06 RAM-only close draft');
  await attachReady(page);
  await page.getByTestId('close-ai-chat').click();
  await openChat(page);
  await expect(editor(page)).toHaveValue('C06 RAM-only close draft');
  await expect(attachment(page)).toBeVisible();
  const persisted = await page.evaluate(() =>
    Object.values(localStorage).some(
      (value) =>
        value.includes('C06 RAM-only close draft') ||
        value.includes('C06-ready.txt') ||
        value.includes('draft-ready-file')
    )
  );
  expect(persisted).toBe(false);
});

test('C06 revision: editing during a held target preflight cancels send and preserves the newer draft', async ({
  page
}) => {
  const { fixture, chatPosts } = await setup(page);
  fixture.foundation.focus.rows['kanban_card:11'] = {
    kind: 'kanban_card',
    id: 11,
    label: 'C06 confirmed task',
    machine_id: 7,
    work_order_id: 5
  };
  let release = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = false;
  let completed = false;
  await page.route('**/api/ai/threads/*/focus', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    started = true;
    await held;
    await route.fallback();
    completed = true;
  });
  try {
    await page.getByTestId('fixture-ask-task').click();
    await editor(page).fill('C06 original prepared draft');
    await page.getByTestId('send-ai-chat-message').click();
    await expect.poll(() => started).toBe(true);
    await expect(editor(page)).toBeEnabled();
    await editor(page).fill('C06 newer edited draft');
    release();
    await expect.poll(() => completed).toBe(true);
    await expect(page.getByTestId('conversation-focus-label')).toHaveText(
      'C06 confirmed task'
    );
    await expect(editor(page)).toHaveValue('C06 newer edited draft');
    await expect(page.getByTestId('send-ai-chat-message')).toBeEnabled();
    expect(chatPosts).toEqual([]);
  } finally {
    release();
  }
});

test('C06 positive: the real session reset drops drafts and ready references before returning to the same thread', async ({
  page
}) => {
  await setup(page);
  await editor(page).fill('C06 previous session draft');
  await attachReady(page);
  await page.evaluate(async () => {
    const moduleUrl = '/src/states/AIChatState.tsx';
    const { useAIChatState } = await import(moduleUrl);
    useAIChatState.getState().resetSession(false);
  });
  await openChat(page);
  await select(page, 'A');
  await expect(editor(page)).toHaveValue('');
  await expect(attachment(page)).toHaveCount(0);
});

test('C06 positive: explicit deletion retires visible content and ready attachment controls', async ({
  page
}) => {
  await setup(page);
  await editor(page).fill('C06 deleted private draft');
  await attachReady(page);
  await page.getByTestId('select-ai-chat-thread').click();
  await page.getByTestId(`delete-ai-chat-thread-${threadId}`).click();
  const dialog = page.getByRole('dialog', { name: 'Delete conversation?' });
  await dialog
    .getByRole('button', { name: 'Delete conversation', exact: true })
    .click();
  await expect(dialog).toBeHidden();
  await expect(editor(page)).toHaveValue('');
  await expect(attachment(page)).toHaveCount(0);
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await expect(
    page
      .getByTestId('ai-chat-history-row')
      .filter({ hasText: 'C06 Conversation A' })
  ).toHaveCount(0);
  await expect(
    page
      .getByTestId('ai-chat-history-row')
      .filter({ hasText: 'C06 Conversation B' })
  ).toBeVisible();
});
