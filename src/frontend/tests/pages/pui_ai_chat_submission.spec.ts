/** Common send admission: real drawer/cards, wholly mocked auth and APIs. */
import { type Page, expect, test } from '@playwright/test';

import {
  loadDrawerFixture,
  prepareDrawerFixture
} from './ai_drawer_harness.js';
import { goldenEvents, sseBody, threadId } from './aichat_harness.js';

const editor = (page: Page) =>
  page.getByRole('textbox', { name: 'Message', exact: true });
const fileId = 'submission-draft-file';
const attachment = (page: Page) =>
  page.getByTestId(`remove-ai-chat-attachment-${fileId}`);
const option = (page: Page) => page.getByTestId('question-option-machine:7');
const question = {
  kind: 'clarification_question',
  interrupt_id: 'submission-question',
  question_text: 'Which pump should I inspect?',
  options: [
    { id: 'machine:7', kind: 'machine', label: 'Pump A' },
    { id: 'machine:8', kind: 'machine', label: 'Pump B' }
  ],
  expires_at: '2099-01-01T00:00:00Z',
  source: 'manual_search_ambiguity'
};

function gate() {
  let release = () => {};
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function setup(page: Page, withQuestion = true) {
  const fixture = await prepareDrawerFixture(page, {
    voice: false,
    tab: 'chat',
    foundation: {
      threadDetailMessages: withQuestion
        ? [
            {
              id: 'submission-question-message',
              role: 'assistant',
              content: 'Which pump should I inspect?',
              timestamp: '2026-10-24T00:00:00Z',
              question
            }
          ]
        : []
    }
  });
  const posts: { message: string; thread_id: string; file_ids?: string[] }[] =
    [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/ai/**', async (route) => {
    const request = route.request();
    if (
      request.method() === 'POST' &&
      /\/(chat(?:\/stream)?|agui)\/?$/.test(new URL(request.url()).pathname)
    ) {
      posts.push(request.postDataJSON());
      await route.fulfill({
        contentType: 'text/event-stream',
        body: sseBody(goldenEvents)
      });
      return;
    }
    await route.fallback();
  });
  await page.route('**/api/ai/upload', async (route) => {
    await route.fulfill({
      json: {
        file_id: fileId,
        filename: 'unsent-manual.txt',
        size: 12,
        content_type: 'text/plain',
        thread_id: threadId
      }
    });
  });
  await loadDrawerFixture(page);
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: 'Server conversation' })
    .click();
  await expect(editor(page)).toBeEnabled();
  if (withQuestion)
    await expect(page.getByTestId('question-card')).toBeVisible();
  else
    await expect(page.getByTestId('ai-chat-suggestion').first()).toBeVisible();
  return { fixture, posts, errors };
}

async function attach(page: Page) {
  await page.locator('input[type=file]').setInputFiles({
    name: 'unsent-manual.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('Mock manual')
  });
}

async function assertDraft(page: Page, text: string) {
  await expect(editor(page)).toHaveValue(text);
  await expect(attachment(page)).toBeVisible();
}

test('common send: a suggestion preserves the unrelated composer text and ready attachment', async ({
  page
}, testInfo) => {
  const { posts, errors } = await setup(page, false);
  const draft = 'My unsent maintenance question';
  await editor(page).fill(draft);
  await attach(page);
  await expect(attachment(page)).toBeVisible();
  await page.getByRole('button', { name: 'Search parts', exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({
    thread_id: threadId,
    message: 'Search for parts in inventory'
  });
  expect(posts[0].file_ids ?? []).toEqual([]);
  await assertDraft(page, draft);
  await page.screenshot({
    path: testInfo.outputPath('suggestion-draft-preserved.png')
  });
  await expect(editor(page)).toBeEnabled();
  await page.getByTestId('send-ai-chat-message').click();
  await expect.poll(() => posts.length).toBe(2);
  expect(posts[1]).toMatchObject({ message: draft, file_ids: [fileId] });
  await expect(editor(page)).toHaveValue('');
  await expect(attachment(page)).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('common send: an admitted question answer preserves the composer and sends no implicit files', async ({
  page
}) => {
  const { posts, errors } = await setup(page);
  const draft = 'Keep this separate repair question';
  await editor(page).fill(draft);
  await attach(page);
  await expect(attachment(page)).toBeVisible();
  await option(page).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({ message: 'Pump A', thread_id: threadId });
  expect(posts[0].file_ids ?? []).toEqual([]);
  await expect(page.getByTestId('question-card-frozen')).toBeVisible();
  await assertDraft(page, draft);
  expect(errors).toEqual([]);
});

test('common send: a blocked question answer remains armed and can be retried after upload', async ({
  page
}) => {
  const { posts, errors } = await setup(page);
  const upload = gate();
  let started = false;
  await page.route('**/api/ai/upload', async (route) => {
    started = true;
    await upload.promise;
    await route.fallback();
  });
  try {
    await editor(page).fill('Keep the uploading draft');
    await attach(page);
    await expect.poll(() => started).toBe(true);
    await option(page).click();
    await expect(page.getByTestId('question-card')).toBeVisible();
    await expect(option(page)).toBeEnabled();
    expect(posts).toEqual([]);
    upload.release();
    await assertDraft(page, 'Keep the uploading draft');
    await option(page).click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0].file_ids ?? []).toEqual([]);
    await assertDraft(page, 'Keep the uploading draft');
    expect(errors).toEqual([]);
  } finally {
    upload.release();
  }
});

test('common send: context refusal leaves the question retryable and the unsent draft intact', async ({
  page
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: 'dark' });
  const { fixture, posts, errors } = await setup(page);
  fixture.foundation.focus.rejectUpdates = true;
  // The mobile drawer correctly isolates the page's contextual Ask buttons.
  await page.getByTestId('close-ai-chat').click();
  await page.getByTestId('fixture-ask-task').click();
  await editor(page).fill('Retain my separate draft');
  await attach(page);
  await expect(attachment(page)).toBeVisible();
  await option(page).click();
  await expect.poll(() => fixture.foundation.focusMutations.length).toBe(1);
  await expect(page.getByTestId('question-card')).toBeVisible();
  await expect(option(page)).toBeEnabled();
  expect(posts).toEqual([]);
  await assertDraft(page, 'Retain my separate draft');
  await expect(
    page.getByText('The answer was not sent. You can try again.', {
      exact: true
    })
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('question-refusal-mobile-dark.png')
  });
  fixture.foundation.focus.rejectUpdates = false;
  await option(page).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(fixture.foundation.focusMutations).toHaveLength(2);
  await assertDraft(page, 'Retain my separate draft');
  expect(errors).toEqual([]);
});

test('common send: editing during question preflight cancels that answer and releases it for an explicit retry', async ({
  page
}) => {
  const { fixture, posts } = await setup(page);
  const focus = gate();
  let started = false;
  let finished = false;
  await page.route('**/api/ai/threads/*/focus', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    started = true;
    await focus.promise;
    await route.fallback();
    finished = true;
  });
  try {
    await page.getByTestId('fixture-ask-task').click();
    await editor(page).fill('Original composer text');
    await option(page).click();
    await expect.poll(() => started).toBe(true);
    await expect(option(page)).toBeDisabled();
    await editor(page).fill('Newer composer text');
    focus.release();
    await expect.poll(() => finished).toBe(true);
    await expect(option(page)).toBeEnabled();
    expect(posts).toEqual([]);
    expect(fixture.foundation.focusMutations).toHaveLength(1);
    await expect(editor(page)).toHaveValue('Newer composer text');
    await expect(page.getByTestId('ai-chat-send-notice')).toContainText(
      'Your draft changed'
    );
    await editor(page).fill('Newest composer text');
    await expect(page.getByTestId('ai-chat-send-notice')).toHaveCount(0);
    await option(page).dblclick();
    await expect.poll(() => posts.length).toBe(1);
    // One preflight for the canceled attempt, one for the explicit retry.
    expect(fixture.foundation.focusMutations).toHaveLength(2);
    await expect(editor(page)).toHaveValue('Newest composer text');
  } finally {
    focus.release();
  }
});

test('common send: Other ignores Shift+Enter and composition, then admits plain Enter once', async ({
  page
}) => {
  const { posts, errors } = await setup(page);
  await editor(page).fill('Separate composer draft');
  const other = page.getByLabel('question-other-input');
  await other.fill('Inspect the standby pump');
  await other.press('Shift+Enter');
  await expect(other).toHaveValue('Inspect the standby pump');
  await expect(page.getByTestId('question-card')).toBeVisible();
  expect(posts).toEqual([]);
  await other.dispatchEvent('keydown', {
    key: 'Enter',
    isComposing: true,
    keyCode: 229,
    bubbles: true
  });
  await expect(other).toHaveValue('Inspect the standby pump');
  expect(posts).toEqual([]);
  await other.press('Enter');
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].message).toBe('Inspect the standby pump');
  await expect(editor(page)).toHaveValue('Separate composer draft');
  expect(errors).toEqual([]);
});
