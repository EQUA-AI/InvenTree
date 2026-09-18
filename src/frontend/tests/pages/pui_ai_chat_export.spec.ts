/** Deferred browser download qualification; all transcript export traffic is mocked. */
import { readFile } from 'node:fs/promises';
import type { Page } from '@playwright/test';
import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
import { mockChatFoundation, openChat } from './aichat_harness.js';

async function fixture(page: Page) {
  // Read the logged-in test identity; exported content below is synthetic.
  const response = await page.request.get(
    new URL('/api/user/me/', page.url()).toString()
  );
  expect(response.ok()).toBe(true);
  const owner = String((await response.json()).pk);
  return {
    schema_version: 1,
    records: [
      {
        type: 'manifest',
        schema_version: 1,
        scope: 'owned_thread_transcripts',
        owner_id: owner,
        consistency: 'live_read_with_creation_cutoff',
        includes: ['messages'],
        excludes: ['shared_threads', 'attachments']
      },
      {
        type: 'thread',
        thread_id: 'thread_export_only',
        title: 'Export fixture',
        summary: '',
        message_watermark: 1
      },
      {
        type: 'message',
        thread_id: 'thread_export_only',
        message_id: 'export_message',
        sequence: 1,
        content: 'Download-only café 機械',
        role: 'assistant'
      },
      { type: 'complete', threads: 1, messages: 1 }
    ]
  };
}

async function openExport(page: Page) {
  const item = page.getByLabel('export-owned-ai-chat-threads');
  if (!(await item.isVisible()))
    await page.getByLabel('select-ai-chat-thread').click();
  await item.click();
  return page.getByRole('dialog', {
    name: 'Export my saved conversations',
    exact: true
  });
}

test('export is explicit and downloads complete JSON without persisting transcript contents', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const value = await fixture(page);
  let requests = 0;
  await page.route('**/api/ai/threads/export', async (route) => {
    requests++;
    expect(route.request().method()).toBe('POST');
    await route.fulfill({ json: value });
  });
  await page.reload();
  await openChat(page);
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  let modal = await openExport(page);
  await modal.getByRole('button', { name: 'Close', exact: true }).click();
  expect(requests).toBe(0);
  modal = await openExport(page);
  const downloadEvent = page.waitForEvent('download');
  await modal
    .getByRole('button', { name: 'Download JSON', exact: true })
    .click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('aimms-conversations.json');
  const path = await download.path();
  expect(path).not.toBeNull();
  expect(JSON.parse(await readFile(path!, 'utf-8'))).toEqual(value);
  await expect(
    modal.getByText(/Download started: 1 conversations and 1 messages/)
  ).toBeVisible();
  expect(requests).toBe(1);
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(
    'Download-only'
  );
});

test('oversized and incomplete exports show errors without starting downloads', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const value = await fixture(page);
  let requests = 0;
  let downloads = 0;
  page.on('download', () => {
    downloads++;
  });
  await page.route('**/api/ai/threads/export', async (route) => {
    requests++;
    if (requests === 1)
      return route.fulfill({ status: 413, json: { detail: 'limit' } });
    await route.fulfill({
      json: { ...value, records: value.records.slice(0, -1) }
    });
  });
  await page.reload();
  await openChat(page);
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  const modal = await openExport(page);
  await modal
    .getByRole('button', { name: 'Download JSON', exact: true })
    .click();
  await expect(modal.getByRole('alert')).toContainText(
    'too large for a browser download'
  );
  await modal
    .getByRole('button', { name: 'Download JSON', exact: true })
    .click();
  await expect(modal.getByRole('alert')).toContainText(
    'No download was started'
  );
  expect(downloads).toBe(0);
});

test('session reset during export cancels the eventual file handoff', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const value = await fixture(page);
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let requested = false;
  let settled = false;
  let downloads = 0;
  page.on('download', () => {
    downloads++;
  });
  await page.route('**/api/ai/threads/export', async (route) => {
    requested = true;
    await held;
    await route.fulfill({ json: value }).catch(() => {});
    settled = true;
  });
  await page.reload();
  await openChat(page);
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  const modal = await openExport(page);
  await modal
    .getByRole('button', { name: 'Download JSON', exact: true })
    .click();
  await expect.poll(() => requested).toBe(true);
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
  expect(downloads).toBe(0);
});
