/** Authored for the consolidated validation period; all AI traffic is mocked. */
import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
import { mockChatFoundation, openChat, threadId } from './aichat_harness.js';

test('delete cancellation sends no mutation and incomplete cleanup can be retried', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  let attempts = 0;
  await page.route('**/api/ai/threads/**', async (route) => {
    if (route.request().method() !== 'DELETE') return route.fallback();
    attempts += 1;
    await route.fulfill({
      status: attempts === 1 ? 202 : 200,
      json: {
        thread_id: threadId,
        status: attempts === 1 ? 'purge_incomplete' : 'deleted'
      }
    });
  });
  await page.evaluate(() => localStorage.removeItem('ai-chat-threads'));
  await page.reload();
  await openChat(page);
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  await page.getByLabel('select-ai-chat-thread').click();
  await page.getByLabel(`delete-ai-chat-thread-${threadId}`).click();
  let modal = page.getByRole('dialog', { name: 'Delete conversation?' });
  await modal.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(attempts).toBe(0);
  const deleteButton = page.getByLabel(`delete-ai-chat-thread-${threadId}`);
  if (!(await deleteButton.isVisible()))
    await page.getByLabel('select-ai-chat-thread').click();
  await deleteButton.click();
  modal = page.getByRole('dialog', { name: 'Delete conversation?' });
  await modal
    .getByRole('button', { name: 'Delete conversation', exact: true })
    .click();
  const retry = page.getByRole('dialog', {
    name: 'Finish deleting conversation'
  });
  await expect(retry).toBeVisible();
  expect(attempts).toBe(1);
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toHaveCount(0);
  await retry
    .getByRole('button', { name: 'Retry cleanup', exact: true })
    .click();
  await expect(retry).toBeHidden();
  expect(attempts).toBe(2);
});

test('older transcript pages prepend once and exhaust the cursor', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const cursors: string[] = [];
  await page.route('**/api/ai/threads/**', async (route) => {
    const url = new URL(route.request().url());
    if (
      route.request().method() !== 'GET' ||
      !url.pathname.endsWith(`/threads/${threadId}`)
    )
      return route.fallback();
    const before = url.searchParams.get('before_sequence');
    if (before) cursors.push(before);
    const sequence = before ? 1 : 2;
    await route.fulfill({
      json: {
        thread_id: threadId,
        title: 'Paged conversation',
        created_at: '2026-09-17T00:00:00Z',
        updated_at: '2026-09-17T00:00:00Z',
        messages: [
          {
            id: `message-${sequence}`,
            sequence,
            role: 'assistant',
            content: before
              ? 'Earlier answer fixture'
              : 'Latest answer fixture',
            timestamp: '2026-09-17T00:00:00Z'
          }
        ],
        has_earlier: !before,
        next_before_sequence: before ? null : 2
      }
    });
  });
  await page.evaluate(() => localStorage.removeItem('ai-chat-threads'));
  await page.reload();
  await openChat(page);
  await expect(
    page.getByText('Latest answer fixture', { exact: true })
  ).toBeVisible();
  await page.getByRole('button', { name: 'Load earlier messages' }).click();
  await expect(
    page.getByText('Earlier answer fixture', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Latest answer fixture', { exact: true })
  ).toHaveCount(1);
  await expect(
    page.getByRole('button', { name: 'Load earlier messages' })
  ).toHaveCount(0);
  expect(cursors).toEqual(['2']);
});

test('a delayed conversation response cannot replace a later selection', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  let releaseB!: () => void;
  const waitForB = new Promise<void>((resolve) => {
    releaseB = resolve;
  });
  let requestedB = false;
  let fulfilledB = false;
  await page.route('**/api/ai/threads**', async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() !== 'GET' || url.pathname.endsWith('/scope'))
      return route.fallback();
    const stamp = '2026-09-17T00:00:00Z';
    if (url.pathname.endsWith('/threads')) {
      return route.fulfill({
        json: {
          threads: [threadId, 'thread-lifecycle-b'].map((id) => ({
            thread_id: id,
            title: id === threadId ? 'Conversation A' : 'Conversation B',
            created_at: stamp,
            last_activity: stamp,
            is_persisted: true
          })),
          shared_threads: [],
          has_more: false,
          next_cursor: null,
          capabilities: {}
        }
      });
    }
    if (url.pathname.endsWith('/thread-lifecycle-b')) {
      requestedB = true;
      await waitForB;
      await route.fulfill({
        json: {
          thread_id: 'thread-lifecycle-b',
          title: 'Conversation B',
          created_at: stamp,
          updated_at: stamp,
          messages: [
            {
              id: 'late-b',
              role: 'assistant',
              content: 'Delayed B answer',
              timestamp: stamp
            }
          ]
        }
      });
      fulfilledB = true;
      return;
    }
    return route.fallback();
  });
  await page.evaluate(() => localStorage.removeItem('ai-chat-threads'));
  await page.reload();
  await openChat(page);
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: 'Conversation B' })
    .click();
  await expect.poll(() => requestedB).toBe(true);
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  // The first detail projection replaces A's listing title with this title.
  await page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: 'Server conversation' })
    .click();
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  releaseB();
  await expect.poll(() => fulfilledB).toBe(true);
  await expect(page.getByText('Delayed B answer', { exact: true })).toHaveCount(
    0
  );
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
});
