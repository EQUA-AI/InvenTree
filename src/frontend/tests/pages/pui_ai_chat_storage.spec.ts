/** Regression assets for deferred validation; no real model traffic. */
import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
import {
  mockChatFoundation,
  openChat,
  seedLegacyHistory,
  threadId
} from './aichat_harness.js';

test('legacy browser history stays isolated until explicit backup and removal', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  const observations = await mockChatFoundation(page);
  await seedLegacyHistory(page);
  const original = await page.evaluate(() =>
    localStorage.getItem('ai-chat-threads')
  );
  await page.reload();
  await openChat(page);
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Local-only compatible history', { exact: true })
  ).toHaveCount(0);
  await expect(
    page.getByText('Stale persisted local history', { exact: true })
  ).toHaveCount(0);
  await page.getByRole('button', { name: 'Review browser history' }).click();
  const dialog = page.getByRole('dialog', { name: 'Older browser history' });
  const remove = dialog.getByRole('button', {
    name: 'Remove older browser history'
  });
  await expect(remove).toBeDisabled();
  const downloading = page.waitForEvent('download');
  await dialog
    .getByRole('button', { name: 'Download browser history' })
    .click();
  const download = await downloading;
  expect(download.suggestedFilename()).toBe(
    'aimms-legacy-browser-conversations.json'
  );
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).toString('utf8')).toBe(original);
  expect(
    await page.evaluate(() => localStorage.getItem('ai-chat-threads'))
  ).toBe(original);
  await dialog.getByRole('checkbox').check();
  await remove.click();
  await expect(dialog).toBeHidden();
  expect(
    await page.evaluate(() => localStorage.getItem('ai-chat-threads'))
  ).toBeNull();
  expect(observations.threadMutations).toHaveLength(0);
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
});

test('a delayed transcript cannot refill a session cleared from another tab', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  let release = () => {};
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  let oldSession = true;
  let heldRequests = 0;
  let finishedOldRequests = 0;
  await page.route('**/api/ai/threads/**', async (route) => {
    if (
      !new URL(route.request().url()).pathname.endsWith(
        `/threads/${threadId}`
      ) ||
      route.request().method() !== 'GET'
    )
      return route.fallback();
    const wasOld = oldSession;
    if (wasOld) {
      heldRequests += 1;
      await delayed;
    }
    await route.fulfill({
      json: {
        thread_id: threadId,
        title: 'Current conversation',
        created_at: '2026-09-17T00:00:00Z',
        updated_at: '2026-09-17T00:00:00Z',
        messages: [
          {
            id: wasOld ? 'old' : 'current',
            sequence: 1,
            role: 'assistant',
            content: wasOld
              ? 'Delayed old transcript'
              : 'Current authorized transcript',
            timestamp: '2026-09-17T00:00:00Z'
          }
        ]
      }
    });
    if (wasOld) finishedOldRequests += 1;
  });
  await page.reload();
  await openChat(page);
  await expect.poll(() => heldRequests).toBeGreaterThan(0);
  oldSession = false;
  await page.evaluate(() => {
    const key = Object.keys(localStorage).find((key) =>
      key.startsWith('aimms.chat.index:v2:')
    );
    if (!key)
      throw new Error('Expected a metadata index before the detail response');
    const oldValue = localStorage.getItem(key);
    localStorage.removeItem(key);
    window.dispatchEvent(
      new StorageEvent('storage', { key, oldValue, newValue: null })
    );
  });
  await expect(page.getByPlaceholder('Type a message...')).toBeHidden();
  await openChat(page);
  await expect(
    page.getByText('Current authorized transcript', { exact: true })
  ).toBeVisible();
  release();
  await expect.poll(() => finishedOldRequests).toBe(heldRequests);
  await expect(
    page.getByText('Delayed old transcript', { exact: true })
  ).toHaveCount(0);
  const indices = await page.evaluate(() =>
    Object.entries(localStorage)
      .filter(([key]) => key.startsWith('aimms.chat.index:v2:'))
      .map(([, value]) => value)
      .join('')
  );
  expect(indices).not.toContain('transcript');
});

test('a changed client context clears cached content and the unsent composer', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  let context = 'a'.repeat(64);
  await page.route('**/api/ai/threads?**', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    await route.fulfill({
      json: {
        threads: [],
        shared_threads: [],
        has_more: false,
        cache_context: context,
        capabilities: {}
      }
    });
  });
  await page.evaluate(() => {
    localStorage.removeItem('ai-chat-threads');
    for (const key of Object.keys(localStorage))
      if (key.startsWith('aimms.chat.index:v2:')) localStorage.removeItem(key);
    localStorage.setItem('ai-chat-drawer-active-tab', JSON.stringify('chat'));
  });
  await page.reload();
  await openChat(page);
  const composer = page.getByPlaceholder('Type a message...');
  await composer.fill('Private unsent draft');
  context = 'b'.repeat(64);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(composer).toBeHidden();
  await openChat(page);
  await expect(page.getByPlaceholder('Type a message...')).toHaveValue('');
  expect(
    await page.evaluate(() =>
      Object.entries(localStorage)
        .filter(([key]) => key.startsWith('aimms.chat.index:v2:'))
        .some(([, value]) => String(value).includes('Private unsent draft'))
    )
  ).toBe(false);
});
