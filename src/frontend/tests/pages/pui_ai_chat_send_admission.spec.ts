/** C06-L2: permanent send-admission regression; wholly mocked auth/API. */
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

type AdmissionPost = {
  message: string;
  thread_id: string;
  file_ids?: string[];
};

async function admissionWindow(page: Page) {
  const { fixture } = await setup(page);
  fixture.foundation.focus.rows['kanban_card:11'] = {
    kind: 'kanban_card',
    id: 11,
    label: 'L2 confirmed task',
    machine_id: 7,
    work_order_id: 5
  };
  const posts: AdmissionPost[] = [];
  await page.route('**/api/ai/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (
      request.method() === 'POST' &&
      /\/(chat(?:\/stream)?|agui)\/?$/.test(path)
    ) {
      posts.push(request.postDataJSON() as AdmissionPost);
    }
    await route.fallback();
  });
  let releaseFocus = () => {};
  const focusGate = new Promise<void>((resolve) => {
    releaseFocus = resolve;
  });
  let focusStarted = false;
  let focusCompleted = false;
  await page.route('**/api/ai/threads/*/focus', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    focusStarted = true;
    await focusGate;
    await route.fallback();
    focusCompleted = true;
  });
  const deletes: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'DELETE') {
      deletes.push(new URL(request.url()).pathname);
    }
  });
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.evaluate(() => {
    const host = window as typeof window & {
      __l2NativeClicks?: { name: string; trusted: boolean }[];
    };
    host.__l2NativeClicks = [];
    document.addEventListener('click', (event) => {
      const button =
        event.target instanceof Element ? event.target.closest('button') : null;
      if (!button) return;
      const name =
        button.getAttribute('data-testid') ??
        button.getAttribute('aria-label') ??
        button.textContent?.trim() ??
        '';
      if (
        [
          'select-ai-chat-thread',
          'delete-owned-ai-chat-threads',
          'Delete my saved conversations'
        ].includes(name)
      ) {
        host.__l2NativeClicks?.push({ name, trusted: event.isTrusted });
      }
    });
  });
  const text = 'L2 held draft must survive admission rejection';
  await page.getByTestId('fixture-ask-task').click();
  await attachReady(page);
  await editor(page).fill(text);
  const intent = async () =>
    page.evaluate(async () => {
      const moduleUrl = '/src/states/AIChatState.tsx';
      const { useAIChatState } = await import(moduleUrl);
      const token = useAIChatState.getState().targetIntentToken;
      return token ? { generation: token.generation } : null;
    });
  const beforeIntent = await intent();
  expect(beforeIntent).not.toBeNull();
  await page.getByTestId('send-ai-chat-message').click();
  await expect.poll(() => focusStarted).toBe(true);
  await expect(editor(page)).toBeEnabled();
  expect(focusCompleted).toBe(false);
  return {
    text,
    posts,
    deletes,
    errors,
    intent,
    beforeIntent,
    releaseFocus,
    focusCompleted: () => focusCompleted
  };
}

test('C06-L2: a send rejected during held focus preflight retains its original draft files and intent', async ({
  page
}) => {
  const window = await admissionWindow(page);
  let releasePlan = () => {};
  const planGate = new Promise<void>((resolve) => {
    releasePlan = resolve;
  });
  let preparations = 0;
  await page.route('**/api/ai/threads/deletion-plan', async (route) => {
    expect(route.request().method()).toBe('POST');
    preparations += 1;
    await planGate;
    await route.fulfill({
      status: 503,
      json: { detail: 'L2 controlled preparation refusal; zero deletions' }
    });
  });
  try {
    const selector = page.getByRole('button', {
      name: 'Choose a conversation',
      exact: true
    });
    await expect(selector).toBeEnabled();
    await selector.click();
    await page.getByTestId('delete-owned-ai-chat-threads').click();
    const modal = page.getByRole('dialog', {
      name: 'Delete my saved conversations?',
      exact: true
    });
    await expect(modal).toBeVisible();
    await modal
      .getByRole('button', {
        name: 'Delete my saved conversations',
        exact: true
      })
      .click();
    await expect.poll(() => preparations).toBe(1);
    await expect(
      modal.getByRole('button', { name: 'Cancel', exact: true })
    ).toBeDisabled();
    expect(window.focusCompleted()).toBe(false);
    const clicks = await page.evaluate(
      () =>
        (
          globalThis as typeof globalThis & {
            __l2NativeClicks?: { name: string; trusted: boolean }[];
          }
        ).__l2NativeClicks ?? []
    );
    expect(clicks.map((event) => event.name)).toEqual([
      'select-ai-chat-thread',
      'delete-owned-ai-chat-threads',
      'Delete my saved conversations'
    ]);
    expect(clicks.every((event) => event.trusted)).toBe(true);
    window.releaseFocus();
    await expect.poll(window.focusCompleted).toBe(true);
    await expect(page.getByTestId('conversation-focus-label')).toHaveText(
      'L2 confirmed task'
    );
    await expect(
      modal.getByRole('button', { name: 'Cancel', exact: true })
    ).toBeDisabled();
    const whileBusy = {
      text: await editor(page).inputValue(),
      files: await attachment(page).count(),
      intent: await window.intent(),
      posts: [...window.posts]
    };
    releasePlan();
    await expect(modal.getByRole('alert')).toContainText(
      'Deletion could not finish'
    );
    await modal.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(modal).toBeHidden();
    const afterRefusal = {
      text: await editor(page).inputValue(),
      files: await attachment(page).count()
    };
    await select(page, 'B');
    await select(page, 'A');
    const restored = {
      text: await editor(page).inputValue(),
      files: await attachment(page).count()
    };
    console.log(
      `C06_L2_OBSERVATIONS ${JSON.stringify({
        whileBusy,
        afterRefusal,
        restored,
        clicks,
        preparations,
        deletes: window.deletes,
        errors: window.errors,
        beforeIntent: window.beforeIntent
      })}`
    );
    expect(window.posts).toEqual([]);
    expect(window.deletes).toEqual([]);
    expect(window.errors).toEqual([]);
    expect(whileBusy.text, 'L2 rejected send must retain original draft').toBe(
      window.text
    );
    expect(whileBusy.files).toBe(1);
    expect(whileBusy.intent).toEqual(window.beforeIntent);
    expect(afterRefusal).toEqual({ text: window.text, files: 1 });
    expect(restored).toEqual({ text: window.text, files: 1 });
  } finally {
    window.releaseFocus();
    releasePlan();
  }
});

test('C06-L2 positive: an admitted held-focus send preserves the exact snapshot and consumes its draft', async ({
  page
}) => {
  const window = await admissionWindow(page);
  try {
    window.releaseFocus();
    await expect.poll(() => window.posts.length).toBe(1);
    expect(window.posts[0]).toMatchObject({
      message: window.text,
      thread_id: threadId,
      file_ids: [fileId]
    });
    await expect(editor(page)).toHaveValue('');
    await expect(attachment(page)).toHaveCount(0);
    expect(await window.intent()).toBeNull();
    expect(window.deletes).toEqual([]);
    expect(window.errors).toEqual([]);
    console.log(`C06_L2_POSITIVE ${JSON.stringify(window.posts)}`);
  } finally {
    window.releaseFocus();
  }
});
