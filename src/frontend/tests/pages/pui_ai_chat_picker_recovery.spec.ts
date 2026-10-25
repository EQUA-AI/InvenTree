/** Real picker and chat owner; auth and APIs are wholly mocked. */
import { type Page, expect, test } from '@playwright/test';
import {
  loadDrawerFixture,
  prepareDrawerFixture
} from './ai_drawer_harness.js';
import {
  goldenEvents,
  mockFocusCandidates,
  sseBody,
  threadId
} from './aichat_harness.js';

const oldTarget = {
  kind: 'work_order',
  id: 5,
  label: 'Current work order',
  machine_id: 7,
  work_order_id: 5
};
const candidate = {
  kind: 'machine',
  id: 8,
  label: 'Outside pump',
  machine_id: 8,
  work_order_id: null
};
const picker = (page: Page) =>
  page.getByRole('dialog', { name: 'What are we talking about?', exact: true });
const option = (page: Page) =>
  picker(page).getByTestId('conversation-focus-option').first();
const editor = (page: Page) =>
  page.getByRole('textbox', { name: 'Message', exact: true });
const label = (page: Page) =>
  page.getByTestId('ai-chat-drawer').getByTestId('conversation-focus-label');
function gate() {
  let release = () => {};
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}
async function setup(page: Page) {
  const fixture = await prepareDrawerFixture(page, {
    voice: false,
    tab: 'chat',
    foundation: { focus: { focus: oldTarget, version: 4 } }
  });
  Object.assign(fixture.foundation.scope, {
    mode: 'explicit_assets',
    version: 3,
    machineIds: [7],
    displayLabel: 'Current pump'
  });
  await mockFocusCandidates(page, {
    candidates: [candidate, { ...candidate, id: 9, machine_id: 9 }]
  });
  // Only the known persisted thread has server context. Newly minted IDs
  // must not inherit the foundation mock's single mutable focus/scope row.
  await page.route(
    /\/api\/ai\/threads\/[^/]+\/(focus|scope)$/,
    async (route) => {
      const current = new URL(route.request().url()).pathname.split('/').at(-2);
      if (route.request().method() === 'GET' && current !== threadId) {
        await route.fulfill({ status: 404, json: {} });
      } else await route.fallback();
    }
  );
  const posts: Record<string, unknown>[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/ai/chat/stream', async (route) => {
    posts.push(route.request().postDataJSON());
    await route.fulfill({
      contentType: 'text/event-stream',
      body: sseBody(goldenEvents)
    });
  });
  await page.route('**/api/ai/upload', async (route) =>
    route.fulfill({
      json: {
        file_id: 'picker-manual',
        filename: 'retained.txt',
        size: 6,
        content_type: 'text/plain',
        thread_id: threadId
      }
    })
  );
  await loadDrawerFixture(page);
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: 'Server conversation' })
    .click();
  await expect(label(page)).toHaveText('Current work order');
  return { ...fixture, posts, errors };
}
async function openPicker(page: Page) {
  await page
    .getByTestId('ai-chat-drawer')
    .getByTestId('conversation-focus-change')
    .click();
  await expect(picker(page)).toBeVisible();
}

test('picker recovery: search retry repeats the failed query inside the narrow active picker', async ({
  page
}, testInfo) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await page.emulateMedia({ colorScheme: 'dark' });
  const { foundation, errors } = await setup(page);
  let failed = true;
  const searches: string[] = [];
  await page.route('**/api/ai/focus/candidates**', async (route) => {
    const query =
      new URL(route.request().url()).searchParams.get('query') ?? '';
    searches.push(query);
    if (failed) await route.fulfill({ status: 503, json: {} });
    else await route.fallback();
  });
  await openPicker(page);
  await picker(page).getByLabel('Search targets').fill('Outside');
  await expect(picker(page).getByRole('alert')).toContainText(
    'Could not load targets'
  );
  await expect(option(page)).toHaveCount(0);
  failed = false;
  await picker(page)
    .getByRole('button', { name: 'Retry search', exact: true })
    .click();
  await expect(option(page)).toBeEnabled();
  expect(searches.filter((query) => query === 'Outside')).toHaveLength(2);
  expect(foundation.focusMutations).toEqual([]);
  await expect(picker(page).getByLabel('Search targets')).toHaveValue(
    'Outside'
  );
  await page.screenshot({
    path: testInfo.outputPath('picker-search-recovered-320-dark.png')
  });
  await page.keyboard.press('Escape');
  await expect(
    page.getByTestId('conversation-focus-change').first()
  ).toBeFocused();
  await expect(label(page)).toHaveText('Current work order');
  expect(errors).toEqual([]);
});

test('picker recovery: one held selection disables commits and dismissal, then exposes a retryable refusal', async ({
  page
}, testInfo) => {
  const { foundation, errors } = await setup(page);
  await editor(page).fill('Keep my repair draft');
  const held = gate();
  let puts = 0;
  await page.route('**/api/ai/threads/*/focus', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    puts += 1;
    if (puts === 1) {
      await held.promise;
      await route.fulfill({
        status: 400,
        json: { detail: 'focus_update_rejected' }
      });
    } else await route.fallback();
  });
  try {
    await openPicker(page);
    await option(page).dblclick();
    await expect(
      picker(page).getByTestId('conversation-focus-pending')
    ).toBeVisible();
    await expect(option(page)).toBeDisabled();
    await expect(picker(page).getByLabel('Search targets')).toBeDisabled();
    await expect(picker(page).getByLabel('Close target picker')).toBeDisabled();
    await expect(
      picker(page).getByRole('radio', { name: 'Task', exact: true })
    ).toBeDisabled();
    await page.keyboard.press('Escape');
    await expect(picker(page)).toBeVisible();
    expect(puts).toBe(1);
    held.release();
    await expect(picker(page).getByRole('alert')).toContainText(
      'focus was not changed'
    );
    await page.screenshot({
      path: testInfo.outputPath('picker-selection-refused.png')
    });
    await picker(page)
      .getByRole('button', { name: 'Retry selection', exact: true })
      .click();
    await expect(picker(page)).not.toBeVisible();
    expect(puts).toBe(2);
    expect(foundation.focusMutations[0].body).toMatchObject({
      focus: { kind: 'machine', id: 8 },
      expected_focus_version: 4
    });
    await expect(label(page)).toHaveText('machine 8');
    await expect(editor(page)).toHaveValue('Keep my repair draft');
    expect(foundation.scopeMutations).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    held.release();
  }
});

test('picker recovery: a conflict converges from the server and explicit retry quotes its new version', async ({
  page
}) => {
  const { foundation, errors } = await setup(page);
  let puts = 0;
  await page.route('**/api/ai/threads/*/focus', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    puts += 1;
    if (puts === 1) {
      foundation.focus.version = 9;
      foundation.focus.focus = { ...oldTarget, label: 'Changed elsewhere' };
      await route.fulfill({
        status: 409,
        json: { detail: 'focus_version_conflict' }
      });
    } else await route.fallback();
  });
  await openPicker(page);
  await option(page).click();
  await expect(picker(page).getByRole('alert')).toContainText(
    'changed elsewhere'
  );
  expect(foundation.focus.focus?.label).toBe('Changed elsewhere');
  await picker(page)
    .getByRole('button', { name: 'Retry selection', exact: true })
    .click();
  await expect(picker(page)).not.toBeVisible();
  expect(puts).toBe(2);
  expect(foundation.focusMutations[0].body?.expected_focus_version).toBe(9);
  await expect(label(page)).toHaveText('machine 8');
  expect(errors).toEqual([]);
});

test('picker recovery: Keep current target dismisses a scope refusal without any scope or chat write', async ({
  page
}, testInfo) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await page.emulateMedia({ colorScheme: 'dark' });
  const { foundation, posts, errors } = await setup(page);
  await editor(page).fill('Retain this conversation draft');
  let puts = 0;
  await page.route('**/api/ai/threads/*/focus', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    puts += 1;
    await route.fulfill({
      status: 400,
      json: { detail: 'focus_out_of_analysis_scope' }
    });
  });
  await openPicker(page);
  await option(page).click();
  await expect(picker(page).getByRole('alert')).toContainText(
    'outside the current analysis scope'
  );
  await expect(
    picker(page).getByText('Outside pump (Machine · Machine 8 · #8)', {
      exact: true
    })
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('picker-scope-refusal-320-dark.png')
  });
  await picker(page)
    .getByRole('button', { name: 'Keep current target', exact: true })
    .click();
  await expect(picker(page)).not.toBeVisible();
  await expect(
    page.getByTestId('conversation-focus-change').first()
  ).toBeFocused();
  await expect(label(page)).toHaveText('Current work order');
  await expect(editor(page)).toHaveValue('Retain this conversation draft');
  expect(puts).toBe(1);
  expect(posts).toEqual([]);
  expect(foundation.scopeMutations).toEqual([]);
  await openPicker(page);
  await expect(picker(page).getByRole('alert')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('picker recovery: new conversation preserves the old draft and confirms only the new target before sending', async ({
  page
}) => {
  const { foundation, posts, errors } = await setup(page);
  await editor(page).fill('Old unsent repair draft');
  await page.locator('input[type=file]').setInputFiles({
    name: 'retained.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('manual')
  });
  await expect(
    page.getByTestId('remove-ai-chat-attachment-picker-manual')
  ).toBeVisible();
  const newFocusPuts: { thread: string; body: Record<string, unknown> }[] = [];
  const newScopePuts: { thread: string; body: Record<string, unknown> }[] = [];
  await page.route('**/api/ai/threads/*/focus', async (route) => {
    const thread = new URL(route.request().url()).pathname.split('/').at(-2)!;
    if (thread === threadId) {
      if (route.request().method() === 'GET') return route.fallback();
      return route.fulfill({
        status: 400,
        json: { detail: 'focus_out_of_analysis_scope' }
      });
    }
    if (route.request().method() === 'GET')
      return route.fulfill({ status: 404, json: {} });
    newFocusPuts.push({ thread, body: route.request().postDataJSON() });
    await route.fulfill({
      json: {
        focus: { ...candidate, label: 'Confirmed new pump' },
        focus_version: 1,
        focus_unavailable: false
      }
    });
  });
  await page.route('**/api/ai/threads/*/scope', async (route) => {
    const thread = new URL(route.request().url()).pathname.split('/').at(-2)!;
    if (thread === threadId) return route.fallback();
    if (route.request().method() === 'GET')
      return route.fulfill({ status: 404, json: {} });
    const body = route.request().postDataJSON();
    newScopePuts.push({ thread, body });
    await route.fulfill({
      json: {
        thread_id: thread,
        scope: {
          schema_version: 1,
          date_window: { from: null, to: null },
          source_classes: [
            'controlled_document',
            'asset_attachment',
            'work_order',
            'maintenance_record'
          ],
          ...body.scope
        },
        version: 1,
        hash: 'f'.repeat(64),
        display_label: 'Confirmed new pump',
        editable: true
      }
    });
  });
  await openPicker(page);
  await option(page).click();
  await picker(page)
    .getByRole('button', {
      name: 'Start a new conversation for this target',
      exact: true
    })
    .click();
  await expect(picker(page)).not.toBeVisible();
  await expect(editor(page)).toHaveValue('');
  await expect(
    page.getByTestId('remove-ai-chat-attachment-picker-manual')
  ).toHaveCount(0);
  await expect(label(page)).toHaveText('nothing yet');
  expect(newFocusPuts).toEqual([]);
  expect(newScopePuts).toEqual([]);
  await editor(page).fill('Inspect the outside pump');
  await page.getByTestId('send-ai-chat-message').click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].thread_id).not.toBe(threadId);
  expect(posts[0].file_ids ?? []).toEqual([]);
  expect(posts[0].expected_focus_version).toBe(1);
  expect(newScopePuts).toHaveLength(1);
  expect(newFocusPuts).toHaveLength(1);
  expect(newScopePuts[0]).toMatchObject({
    thread: posts[0].thread_id,
    body: { scope: { mode: 'explicit_assets', machine_ids: [8] } }
  });
  expect(newFocusPuts[0]).toMatchObject({
    thread: posts[0].thread_id,
    body: { focus: { kind: 'machine', id: 8 } }
  });
  expect(foundation.scope.machineIds).toEqual([7]);
  expect(foundation.focus.focus).toEqual(oldTarget);
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: 'Server conversation' })
    .click();
  await expect(editor(page)).toHaveValue('Old unsent repair draft');
  await expect(
    page.getByTestId('remove-ai-chat-attachment-picker-manual')
  ).toBeVisible();
  await expect(label(page)).toHaveText('Current work order');
  expect(errors).toEqual([]);
});

test('picker recovery: the hands-free picker uses the same new-conversation owner', async ({
  page
}) => {
  const { posts, errors } = await setup(page);
  await page.route('**/api/ai/threads/*/focus', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    await route.fulfill({
      status: 400,
      json: { detail: 'focus_out_of_analysis_scope' }
    });
  });
  await page.evaluate(() => (window as any).__aiDrawerTest.openHandsFree());
  await page
    .getByTestId('voice-hands-free')
    .getByTestId('conversation-focus-change')
    .click();
  await option(page).click();
  await picker(page)
    .getByRole('button', {
      name: 'Start a new conversation for this target',
      exact: true
    })
    .click();
  await expect(picker(page)).not.toBeVisible();
  await expect(page.getByTestId('voice-hands-free')).not.toBeVisible();
  await expect(editor(page)).toBeVisible();
  await expect(label(page)).toHaveText('nothing yet');
  expect(posts).toEqual([]);
  expect(errors).toEqual([]);
});

test('picker recovery: reset fences a late refusal and registers the new conversation owner for the new session', async ({
  page
}) => {
  const { errors } = await setup(page);
  const held = gate();
  let puts = 0;
  let finished = false;
  await page.route('**/api/ai/threads/*/focus', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    puts += 1;
    if (puts === 1) await held.promise;
    await route.fulfill({
      status: 400,
      json: { detail: 'focus_out_of_analysis_scope' }
    });
    finished = true;
  });
  try {
    await openPicker(page);
    await option(page).click();
    await expect(
      picker(page).getByTestId('conversation-focus-pending')
    ).toBeVisible();
    await page.evaluate(async () => {
      const moduleUrl = '/src/states/AIChatState.tsx';
      const { useAIChatState } = await import(moduleUrl);
      useAIChatState.getState().resetSession(false);
    });
    await page.getByTestId('open-ai-chat').click();
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    await page
      .getByTestId('ai-chat-history-row')
      .filter({ hasText: 'Server conversation' })
      .click();
    held.release();
    await expect.poll(() => finished).toBe(true);
    await expect(picker(page)).not.toBeVisible();
    await expect(label(page)).toHaveText('Current work order');
    await expect(
      page.getByTestId('ai-chat-drawer').getByRole('alert')
    ).toHaveCount(0);
    await openPicker(page);
    await option(page).click();
    await picker(page)
      .getByRole('button', {
        name: 'Start a new conversation for this target',
        exact: true
      })
      .click();
    await expect(picker(page)).not.toBeVisible();
    await expect(label(page)).toHaveText('nothing yet');
    expect(puts).toBe(2);
    expect(errors).toEqual([]);
  } finally {
    held.release();
  }
});
