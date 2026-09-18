/** Deferred bulk-deletion browser regressions; no real deletion/model calls. */
import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
import { mockChatFoundation, openChat, threadId } from './aichat_harness.js';

test('a session reset stops further bulk pages and discards late progress', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let attempts = 0;
  let settled = false;
  const plan = {
    scope: 'owned_threads',
    cutoff: '2026-09-17T12:00:00+00:00',
    request_token: 'old-session-selection'
  };
  await page.route('**/api/ai/threads**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/threads/deletion-plan'))
      return route.fulfill({ json: plan });
    if (
      route.request().method() !== 'DELETE' ||
      !url.pathname.endsWith('/threads')
    )
      return route.fallback();
    attempts++;
    await pending;
    await route
      .fulfill({
        json: {
          ...plan,
          status: 'purge_incomplete',
          next_cursor: 'must-not-follow',
          processed: 1,
          incomplete: 0,
          remaining_threads: 1,
          results: [{ thread_id: threadId, status: 'deleted' }]
        }
      })
      .catch(() => {}); // The session reset aborts the browser request.
    settled = true;
  });
  await page.reload();
  await openChat(page);
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  await page.getByLabel('select-ai-chat-thread').click();
  await page.getByLabel('delete-owned-ai-chat-threads').click();
  const modal = page.getByRole('dialog', {
    name: 'Delete my saved conversations?'
  });
  await modal
    .getByRole('button', { name: 'Delete my saved conversations', exact: true })
    .click();
  await expect.poll(() => attempts).toBe(1);
  // Same event as another tab clearing the chat session's browser metadata.
  await page.evaluate(() =>
    window.dispatchEvent(
      new StorageEvent('storage', { key: null, newValue: null })
    )
  );
  await expect(modal).toBeHidden();
  release();
  await expect.poll(() => settled).toBe(true);
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  expect(attempts).toBe(1);
});

test('bulk deletion cancels safely, retries a lost page and keeps its original selection', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const plan = {
    scope: 'owned_threads',
    cutoff: '2026-09-17T12:00:00+00:00',
    request_token: 'fixture-original-cutoff'
  };
  let preparations = 0;
  const requests: {
    request_token: string;
    cursor: string | null;
    confirm: boolean;
  }[] = [];
  await page.route('**/api/ai/threads**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/threads/deletion-plan')) {
      preparations++;
      return route.fulfill({ json: plan });
    }
    if (
      route.request().method() !== 'DELETE' ||
      !url.pathname.endsWith('/threads')
    )
      return route.fallback();
    requests.push(route.request().postDataJSON());
    if (requests.length === 1) return route.abort('failed');
    const firstPage = requests.length === 2;
    const needsCleanup = requests.length === 3;
    return route.fulfill({
      status: firstPage || needsCleanup ? 202 : 200,
      json: {
        ...plan,
        status: firstPage || needsCleanup ? 'purge_incomplete' : 'deleted',
        next_cursor: firstPage ? 'fixture-page-2' : null,
        processed: firstPage ? 1 : 2,
        incomplete: needsCleanup ? 1 : 0,
        remaining_threads: firstPage ? 1 : 0,
        results: [
          {
            thread_id: firstPage ? threadId : 'thread-not-loaded',
            status: needsCleanup ? 'purge_incomplete' : 'deleted'
          }
        ]
      }
    });
  });
  await page.reload();
  await openChat(page);
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  const openDeletion = async () => {
    const entry = page.getByLabel('delete-owned-ai-chat-threads');
    if (!(await entry.isVisible()))
      await page.getByLabel('select-ai-chat-thread').click();
    await entry.click();
    return page.getByRole('dialog', { name: 'Delete my saved conversations?' });
  };
  let modal = await openDeletion();
  await modal.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(preparations).toBe(0);
  expect(requests).toHaveLength(0);
  modal = await openDeletion();
  await modal
    .getByRole('button', { name: 'Delete my saved conversations', exact: true })
    .click();
  await expect(
    modal.getByRole('button', { name: 'Retry deletion', exact: true })
  ).toBeVisible();
  expect(preparations).toBe(1);
  await modal
    .getByRole('button', { name: 'Retry deletion', exact: true })
    .click();
  await expect(
    modal.getByRole('button', { name: 'Retry cleanup', exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toHaveCount(0);
  await modal
    .getByRole('button', { name: 'Retry cleanup', exact: true })
    .click();
  await expect(
    modal.getByText('Deletion completed for the selected conversations.')
  ).toBeVisible();
  expect(preparations).toBe(1);
  expect(requests.map((request) => request.request_token)).toEqual(
    Array(4).fill(plan.request_token)
  );
  expect(requests.map((request) => request.cursor)).toEqual([
    null,
    null,
    'fixture-page-2',
    null
  ]);
  expect(requests.every((request) => request.confirm)).toBe(true);
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(
    plan.request_token
  );
});

test('bulk deletion rejects an invalid success receipt and stops after a bounded pass', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const plan = {
    scope: 'owned_threads',
    cutoff: '2026-09-17T12:00:00+00:00',
    request_token: 'bounded-selection'
  };
  let attempts = 0;
  await page.route('**/api/ai/threads**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/threads/deletion-plan'))
      return route.fulfill({ json: plan });
    if (
      route.request().method() !== 'DELETE' ||
      !url.pathname.endsWith('/threads')
    )
      return route.fallback();
    attempts++;
    return route.fulfill({
      json: {
        ...plan,
        status: attempts === 1 ? 'deleted' : 'purge_incomplete',
        // A "deleted" status with more work must never render success.
        next_cursor: `more-${attempts}`,
        processed: attempts,
        incomplete: 0,
        remaining_threads: 1,
        results: []
      }
    });
  });
  await page.reload();
  await openChat(page);
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  await page.getByLabel('select-ai-chat-thread').click();
  await page.getByLabel('delete-owned-ai-chat-threads').click();
  const modal = page.getByRole('dialog', {
    name: 'Delete my saved conversations?'
  });
  await modal
    .getByRole('button', { name: 'Delete my saved conversations', exact: true })
    .click();
  await modal
    .getByRole('button', { name: 'Retry deletion', exact: true })
    .click();
  await expect(
    modal.getByRole('button', { name: 'Continue deletion', exact: true })
  ).toBeVisible();
  expect(attempts).toBe(11);
  await expect(
    modal.getByText('Deletion completed for the selected conversations.')
  ).toHaveCount(0);
});
