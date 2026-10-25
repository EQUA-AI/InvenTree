/** M03: cumulative native pointer/keyboard modal regression; wholly mocked auth/API. */
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

type NativePointer = {
  trusted: boolean;
  ownedBackdrop: boolean;
  insideDialog: boolean;
  pointerType: string;
  prevented: boolean;
};
type PointerProbeWindow = Window &
  typeof globalThis & {
    __m03Pointers: NativePointer[];
    __m03NewClicks: boolean[];
  };

async function observeNativePointer(page: Page) {
  const modal = page.getByRole('dialog', {
    name: 'Delete my saved conversations?',
    exact: true
  });
  await modal.evaluate((element) => {
    // Actual installed9.6.1 Overlay.classes.root, not a guessed app selector.
    // ModalBaseContent's inner is the dialog's parent; its sibling is backdrop.
    const backdrop =
      element.parentElement?.parentElement?.querySelector('.m_9814e45f');
    if (!backdrop || backdrop.contains(element)) {
      throw new Error('M03 owned backdrop boundary not found');
    }
    const probe = window as PointerProbeWindow;
    probe.__m03Pointers = [];
    probe.__m03NewClicks = [];
    document.addEventListener('pointerdown', (event) => {
      probe.__m03Pointers.push({
        trusted: event.isTrusted,
        ownedBackdrop: event.target === backdrop,
        insideDialog:
          event.target instanceof Node && element.contains(event.target),
        pointerType: event.pointerType,
        prevented: event.defaultPrevented
      });
    });
    document.addEventListener(
      'click',
      (event) => {
        if (
          event.target instanceof Element &&
          event.target.closest(
            'button[data-testid="new-ai-chat-thread"]:not([role="menuitem"])'
          )
        ) {
          probe.__m03NewClicks.push(event.isTrusted);
        }
      },
      true
    );
  });
  return () =>
    page.evaluate(() => ({
      pointers: (window as PointerProbeWindow).__m03Pointers,
      newClicks: (window as PointerProbeWindow).__m03NewClicks
    }));
}

async function outsidePointer(page: Page) {
  const box = await page
    .locator('button[data-testid="new-ai-chat-thread"]:not([role="menuitem"])')
    .boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
}

async function openIdleDeletion(page: Page) {
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
  return modal;
}

test('M03 busy deletion retains focus after outside pointer and both keyboard directions', async ({
  page
}) => {
  const { modal, release, destructiveRequests } = await busyDeletion(page);
  const io = await observeChat(page);
  const observe = await observeNativePointer(page);
  const observations: Record<string, unknown> = {};
  try {
    const focus = () =>
      modal.evaluate((element) => ({
        inside: element.contains(document.activeElement),
        tag: document.activeElement?.tagName ?? null
      }));
    await expect.poll(async () => (await focus()).inside).toBe(true);
    const focusSteps: boolean[] = [];
    for (const key of [
      ...Array(30).fill('Tab'),
      ...Array(30).fill('Shift+Tab')
    ]) {
      await page.keyboard.press(key);
      focusSteps.push((await focus()).inside);
    }
    expect(focusSteps).toHaveLength(60);
    expect(focusSteps.every(Boolean)).toBe(true);
    observations.focusSteps = focusSteps;
    await outsidePointer(page);
    await expect(modal).toBeVisible();
    const native = await observe();
    expect(native.pointers).toHaveLength(1);
    expect(native.pointers[0]).toMatchObject({
      trusted: true,
      pointerType: 'mouse',
      ownedBackdrop: true,
      insideDialog: false
    });
    expect(native.newClicks).toEqual([]);
    observations.native = native;
    observations.afterOutsidePointer = await focus();
    await page.keyboard.press('Tab');
    observations.afterOutsidePointerTab = await focus();
    await page.keyboard.press('Shift+Tab');
    observations.afterOutsidePointerReverseTab = await focus();
    expect(io.posts).toEqual([]);
    expect(destructiveRequests).toEqual([]);
    expect(
      (observations.afterOutsidePointer as { inside: boolean }).inside,
      'M03 pointer-focus containment'
    ).toBe(true);
    expect(
      (observations.afterOutsidePointerTab as { inside: boolean }).inside,
      'M03 post-pointer Tab containment'
    ).toBe(true);
    expect(
      (observations.afterOutsidePointerReverseTab as { inside: boolean })
        .inside,
      'M03 post-pointer reverse-Tab containment'
    ).toBe(true);
  } finally {
    release();
    console.log(`M03_POINTER_OBSERVATIONS ${JSON.stringify(observations)}`);
  }
});

test('M03 blocked New preserves original binding through refusal before observable idle New', async ({
  page
}) => {
  await setup(page);
  const io = await observeChat(page);
  await editor(page).fill('M03 original unsent draft');
  await attachReady(page);
  const modal = await openIdleDeletion(page);
  const observe = await observeNativePointer(page);
  let release = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let preparations = 0;
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
      json: { detail: 'Controlled M03 preparation refusal' }
    });
  });
  try {
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
    await page.keyboard.press('Escape');
    await expect(modal).toBeVisible();
    let blocked = false;
    try {
      await newControl(page).click({ timeout: 1500 });
    } catch (error) {
      if (error instanceof Error && error.name === 'TimeoutError') {
        blocked = true;
      } else {
        throw error;
      }
    }
    expect(blocked).toBe(true);
    await outsidePointer(page);
    await expect(modal).toBeVisible();
    expect((await observe()).newClicks).toEqual([]);
    expect(io.posts).toEqual([]);
    release();
    await expect(modal.getByRole('alert')).toBeVisible();
    await modal.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(modal).toHaveCount(0);
    // This send must precede New/rebinding, or a retired binding could be hidden.
    await expect(editor(page)).toHaveValue('M03 original unsent draft');
    await expect(attachment(page)).toBeVisible();
    await sendWords(page, 'M03 same binding after refusal', io.posts, 1);
    expect(io.posts[0].threadId).toBe(threadId);
    await expect(page.getByText('L1 reply 1', { exact: true })).toBeVisible();
    await expect(editor(page)).toBeEnabled();
    expect((await observe()).newClicks).toEqual([]);
    await newControl(page).click();
    expect((await observe()).newClicks).toEqual([true]);
    await expect(editor(page)).toHaveValue('');
    await expect(attachment(page)).toHaveCount(0);
    await sendWords(page, 'M03 idle New positive control', io.posts, 2);
    expect(io.posts[1].threadId).not.toBe(threadId);
    await expect(page.getByText('L1 reply 2', { exact: true })).toBeVisible();
    expect(destructiveRequests).toEqual([]);
  } finally {
    release();
  }
});

test('M03 busy content retains native pointer text selection', async ({
  page
}) => {
  const { modal, release, destructiveRequests } = await busyDeletion(page);
  const observe = await observeNativePointer(page);
  try {
    await modal
      .getByRole('heading', {
        name: 'Delete my saved conversations?',
        exact: true
      })
      .dblclick();
    await expect
      .poll(() => page.evaluate(() => window.getSelection()?.toString() ?? ''))
      .not.toBe('');
    expect(
      await modal.evaluate((element) =>
        element.contains(document.activeElement)
      )
    ).toBe(true);
    const native = await observe();
    expect(native.pointers.length).toBeGreaterThan(0);
    expect(native.pointers.every((event) => event.trusted)).toBe(true);
    expect(native.pointers.every((event) => event.insideDialog)).toBe(true);
    expect(native.pointers.every((event) => !event.ownedBackdrop)).toBe(true);
    expect(native.pointers.every((event) => !event.prevented)).toBe(true);
    expect(destructiveRequests).toEqual([]);
  } finally {
    release();
  }
});

test('M03 idle backdrop dismissal remains native and usable', async ({
  page
}) => {
  await setup(page);
  const io = await observeChat(page);
  const modal = await openIdleDeletion(page);
  const observe = await observeNativePointer(page);
  await outsidePointer(page);
  await expect(modal).toHaveCount(0);
  const native = await observe();
  expect(native.pointers).toHaveLength(1);
  expect(native.pointers[0]).toMatchObject({
    trusted: true,
    ownedBackdrop: true,
    insideDialog: false,
    prevented: false
  });
  expect(native.newClicks).toEqual([]);
  expect(io.posts).toEqual([]);
  await newControl(page).click();
  expect((await observe()).newClicks).toEqual([true]);
  await sendWords(page, 'M03 after idle outside dismissal', io.posts, 1);
  expect(io.posts[0].threadId).not.toBe(threadId);
  await expect(page.getByText('L1 reply 1', { exact: true })).toBeVisible();
});
