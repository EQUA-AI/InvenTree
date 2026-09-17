/** Authored for deferred validation; all chat, upload and deletion traffic is mocked. */
import type { Page, Route } from '@playwright/test';
import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
import { mockChatFoundation, openChat, threadId } from './aichat_harness.js';

const secondId = 'thread-state-b';
const stamp = '2026-09-17T00:00:00Z';
type ServerState = { deleted: boolean; incomplete: boolean };

async function mockThreads(
  page: Page,
  state: ServerState,
  earlier?: (route: Route, id: string) => Promise<void>
) {
  await mockChatFoundation(page);
  await page.route('**/api/ai/threads**', async (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split('/').at(-1)!;
    if (route.request().method() === 'DELETE' && id === threadId) {
      state.deleted = true;
      return route.fulfill({
        status: state.incomplete ? 202 : 200,
        json: {
          thread_id: id,
          status: state.incomplete ? 'purge_incomplete' : 'deleted'
        }
      });
    }
    if (route.request().method() !== 'GET') return route.fallback();
    if (id === 'threads') {
      return route.fulfill({
        json: {
          threads: (state.deleted ? [secondId] : [threadId, secondId]).map(
            (id) => ({
              thread_id: id,
              title: id === threadId ? 'Conversation A' : 'Conversation B',
              created_at: stamp,
              last_activity: stamp,
              is_persisted: true
            })
          ),
          shared_threads: [],
          has_more: false,
          capabilities: {}
        }
      });
    }
    if (![threadId, secondId].includes(id)) return route.fallback();
    if (state.deleted && id === threadId)
      return route.fulfill({ status: 404, json: { detail: 'Not found' } });
    if (url.searchParams.has('before_sequence') && earlier)
      return earlier(route, id);
    return route.fulfill({
      json: {
        thread_id: id,
        title: id === threadId ? 'Conversation A' : 'Conversation B',
        created_at: stamp,
        updated_at: stamp,
        messages: [
          {
            id: `${id}-latest`,
            sequence: 2,
            role: 'assistant',
            content: id === threadId ? 'Answer A' : 'Answer B',
            timestamp: stamp
          }
        ],
        has_earlier: true,
        next_before_sequence: 2
      }
    });
  });
}

async function prepare(page: Page) {
  await page.evaluate(() => {
    for (const key of Object.keys(localStorage))
      if (key.startsWith('aimms.chat.index:v2:')) localStorage.removeItem(key);
    localStorage.removeItem('ai-chat-threads');
    localStorage.setItem('ai-chat-drawer-active-tab', JSON.stringify('chat'));
  });
  await page.reload();
  await openChat(page);
  await expect(page.getByText('Answer A', { exact: true })).toBeVisible();
}

async function select(page: Page, title: string) {
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: title })
    .click();
}

test('a failed older-page read clears its transcript, error and cursor stay out of the next chat', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockThreads(
    page,
    { deleted: false, incomplete: false },
    async (route) => {
      await route.fulfill({ status: 404, json: { detail: 'Not found' } });
    }
  );
  await prepare(page);
  await page.getByRole('button', { name: 'Load earlier messages' }).click();
  await expect(
    page.getByText('Conversation could not be loaded. Refresh and try again.', {
      exact: true
    })
  ).toBeVisible();
  await expect(page.getByText('Answer A', { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Load earlier messages' })
  ).toHaveCount(0);
  await select(page, 'Conversation B');
  await expect(page.getByText('Answer B', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Load earlier messages' })
  ).toBeEnabled();
  await expect(
    page.getByText('Conversation could not be loaded. Refresh and try again.', {
      exact: true
    })
  ).toHaveCount(0);
  await select(page, 'Conversation A');
  await expect(page.getByText('Answer A', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Load earlier messages' })
  ).toBeEnabled();
});

test('an upload returning after A to B to A cannot attach or clear a new draft', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockThreads(page, { deleted: false, incomplete: false });
  let release = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = false;
  let finished = false;
  await page.route('**/api/ai/upload', async (route) => {
    started = true;
    await held;
    await route.fulfill({
      json: {
        file_id: 'old-upload',
        filename: 'old.txt',
        content_type: 'text/plain',
        size: 7
      }
    });
    finished = true;
  });
  await prepare(page);
  await page.getByPlaceholder('Type a message...').fill('Unsent draft for A');
  await page.locator('input[type=file]').setInputFiles({
    name: 'old.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('fixture')
  });
  await expect.poll(() => started).toBe(true);
  await select(page, 'Conversation B');
  await expect(page.getByText('Answer B', { exact: true })).toBeVisible();
  await expect(page.getByPlaceholder('Type a message...')).toHaveValue('');
  await expect(page.getByLabel('attach-ai-chat-file')).toBeEnabled();
  await select(page, 'Conversation A');
  await expect(page.getByText('Answer A', { exact: true })).toBeVisible();
  await page
    .getByPlaceholder('Type a message...')
    .fill('New draft after returning');
  release();
  await expect.poll(() => finished).toBe(true);
  await expect(
    page.getByLabel('remove-ai-chat-attachment-old-upload')
  ).toHaveCount(0);
  await expect(page.getByPlaceholder('Type a message...')).toHaveValue(
    'New draft after returning'
  );
});

for (const incomplete of [false, true]) {
  test(`deletion resets another tab while preserving ${incomplete ? 'the cleanup retry' : 'the remaining conversation'}`, async ({
    browser
  }) => {
    const first = await doCachedLogin(browser);
    const state = { deleted: false, incomplete };
    await mockThreads(first, state);
    await prepare(first);
    const second = await first.context().newPage();
    await mockThreads(second, state);
    await second.goto(first.url());
    await openChat(second);
    await expect(second.getByText('Answer A', { exact: true })).toBeVisible();
    await first.getByLabel('select-ai-chat-thread').click();
    await first.getByLabel(`delete-ai-chat-thread-${threadId}`).click();
    await first
      .getByRole('dialog', { name: 'Delete conversation?' })
      .getByRole('button', { name: 'Delete conversation', exact: true })
      .click();
    if (incomplete)
      await expect(
        first.getByRole('dialog', { name: 'Finish deleting conversation' })
      ).toBeVisible();
    await expect(second.getByPlaceholder('Type a message...')).toBeHidden();
    await openChat(second);
    await expect(second.getByText('Answer A', { exact: true })).toHaveCount(0);
    await second.getByLabel('select-ai-chat-thread').click();
    if (incomplete) {
      await second.getByLabel(`delete-ai-chat-thread-${threadId}`).click();
      const retry = second.getByRole('dialog', {
        name: 'Finish deleting conversation'
      });
      await expect(retry).toBeVisible();
      state.incomplete = false;
      await retry
        .getByRole('button', { name: 'Retry cleanup', exact: true })
        .click();
      await expect(retry).toBeHidden();
    } else {
      await expect(
        second.getByLabel(`delete-ai-chat-thread-${threadId}`)
      ).toHaveCount(0);
      await expect(
        second.getByText('Conversation B', { exact: true }).last()
      ).toBeVisible();
    }
    await second.close();
  });
}
