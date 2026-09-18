/** Authored memory UI qualification; routes are synthetic and no providers run. */
import { expect, test } from '../baseFixtures.js';
import { navigate } from '../helpers.js';
import { doCachedLogin } from '../login.js';

const status = {
  notice_version: 'memory-v1',
  notice_text: 'Fixture memory disclosure.',
  notice_available: true,
  acknowledged: true,
  opted_out: false,
  extraction_enabled: true,
  recall_enabled: false,
  restore_hold: false,
  can_write: true,
  cleanup_pending: false
};
const fact = {
  id: 'c4d4f523-d8d0-4fe9-af4b-fb6ec5c48211',
  version: 3,
  text: 'Fixture preference for a maintenance checklist.',
  text_lang: 'en',
  canonical_unit: '',
  memory_type: 'user_preference',
  topics: ['planning'],
  state: 'proposed',
  verification: 'inferred',
  shield_state: 'clear',
  source_available: false,
  origin: 'compaction',
  last_verified_at: null
};

test('durable memory requires review and submits the displayed preview hash', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  let decisions = 0;
  await page.route('**/api/aichat/memory/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/excluded-conversations/'))
      return route.fulfill({ json: { results: [], next_cursor: null } });
    if (path.endsWith('/settings/')) return route.fulfill({ json: status });
    if (path.endsWith('/facts/'))
      return route.fulfill({ json: { results: [fact], next_cursor: null } });
    if (path.endsWith('/proposals/')) {
      expect(request.postDataJSON()).toMatchObject({
        action_type: 'memory.remember',
        memory_fact_id: fact.id,
        expected_version: 3
      });
      return route.fulfill({
        json: {
          id: 'fixture-proposal',
          action_type: 'memory.remember',
          preview_hash: 'exact-fixture-hash',
          preview: {
            text: fact.text,
            topics: fact.topics,
            untrusted: true,
            shield_state: 'clear'
          }
        }
      });
    }
    if (path.endsWith('/decision/')) {
      decisions += 1;
      expect(request.postDataJSON()).toMatchObject({
        decision: 'confirm',
        expected_preview_hash: 'exact-fixture-hash'
      });
      return route.fulfill({ json: { receipt: { status: 'remembered' } } });
    }
    return route.abort();
  });
  await navigate(page, 'memory/');
  await expect(page.getByText(fact.text)).toBeVisible();
  await page.getByRole('button', { name: 'Review suggestion' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText(fact.text)).toBeVisible();
  await expect(
    dialog.getByRole('button', { name: 'Confirm', exact: true })
  ).toBeDisabled();
  expect(decisions).toBe(0);
  await dialog.getByLabel('I have reviewed this exact action.').check();
  await dialog.getByRole('button', { name: 'Confirm', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(decisions).toBe(1);
  const persisted = await page.evaluate(() =>
    JSON.stringify([Object.values(localStorage), Object.values(sessionStorage)])
  );
  expect(persisted).not.toContain(fact.text);
});

test('failed reauthorization clears displayed memory and hides server error text', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  let denied = false;
  await page.route('**/api/aichat/memory/**', async (route) => {
    if (
      new URL(route.request().url()).pathname.endsWith(
        '/excluded-conversations/'
      )
    )
      return route.fulfill({ json: { results: [], next_cursor: null } });
    if (new URL(route.request().url()).pathname.endsWith('/settings/'))
      return route.fulfill({ json: status });
    return denied
      ? route.fulfill({ status: 403, json: { detail: 'PRIVATE ERROR BODY' } })
      : route.fulfill({ json: { results: [fact], next_cursor: null } });
  });
  await navigate(page, 'memory/');
  await expect(page.getByText(fact.text)).toBeVisible();
  denied = true;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText(fact.text)).not.toBeVisible();
  await expect(page.getByText('PRIVATE ERROR BODY')).not.toBeVisible();
  await expect(
    page.getByText(/Memory could not be loaded or changed/)
  ).toBeVisible();
});

test('excluded conversation uses a single canonical learning mode change', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  let mode = 'off';
  let changes = 0;
  await page.route('**/api/aichat/memory/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/settings/')) return route.fulfill({ json: status });
    if (path.endsWith('/excluded-conversations/'))
      return route.fulfill({
        json: {
          results: [
            {
              thread_id: 'fixture-thread',
              created_at: '2026-09-18T00:00:00Z',
              memory_mode: mode,
              extraction_status: 'memory_off'
            }
          ],
          next_cursor: null
        }
      });
    if (path.endsWith('/conversations/fixture-thread/mode/')) {
      changes += 1;
      expect(route.request().method()).toBe('PUT');
      expect(route.request().postDataJSON()).toEqual({
        memory_mode: 'extract'
      });
      mode = 'extract';
      return route.fulfill({ json: {} });
    }
    return route.fulfill({ json: { results: [], next_cursor: null } });
  });
  await navigate(page, 'memory/');
  await page
    .getByLabel('Learn memories from this conversation')
    .selectOption('extract');
  await expect(
    page.getByLabel('Learn memories from this conversation')
  ).toHaveValue('extract');
  expect(changes).toBe(1);
});

test('private memory review link loads the exact proposal and cannot auto-confirm', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  const id = 'b2fd0a47-2c39-4ecb-a1dd-c3238751fdab';
  let decisions = 0;
  await page.route('**/api/aichat/memory/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/settings/')) return route.fulfill({ json: status });
    if (path.endsWith(`/proposals/${id}/decision/`)) {
      if (request.method() === 'POST') decisions += 1;
      return route.fulfill({
        json: {
          id,
          action_type: 'memory.forget_all',
          preview_hash: 'exact',
          preview: { confirm_phrase: 'forget all my memories', count: 2 }
        }
      });
    }
    return route.fulfill({ json: { results: [], next_cursor: null } });
  });
  await navigate(page, `memory/?proposal=${id}`);
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole('button', { name: 'Confirm', exact: true })
  ).toBeDisabled();
  expect(decisions).toBe(0);
});
