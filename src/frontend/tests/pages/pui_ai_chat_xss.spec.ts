/**
 * Output-rendering safety: hostile model output stays inert (threat 9).
 *
 * MarkdownMessage renders assistant text with react-markdown WITHOUT
 * rehype-raw (raw HTML is text, never DOM) and downgrades any
 * non-http(s) href to plain text. Nothing pinned those properties until
 * this spec — a dependency or component edit must fail here, not ship.
 */

import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
import {
  type SSEEvent,
  mockChatFoundation,
  openChat,
  sseBody,
  threadId
} from './aichat_harness.js';

const HOSTILE_MARKDOWN = [
  'Before <script>window.__xss = true</script> after.',
  '<img src=x onerror="window.__xss = true">',
  '<iframe src="https://evil.example"></iframe>',
  'Click [here](javascript:alert(1)) to finish.',
  'Relative [link](/inert/path) stays text too.',
  'A real [manual link](https://example.com/manual.pdf) survives.'
].join('\n\n');

function hostileEvents(): SSEEvent[] {
  return [
    { type: 'RUN_STARTED', threadId, runId: 'run-xss' },
    {
      type: 'TEXT_MESSAGE_START',
      threadId,
      runId: 'run-xss',
      messageId: 'message-xss',
      role: 'assistant'
    },
    {
      type: 'TEXT_MESSAGE_CONTENT',
      threadId,
      runId: 'run-xss',
      messageId: 'message-xss',
      delta: HOSTILE_MARKDOWN
    },
    {
      type: 'TEXT_MESSAGE_END',
      threadId,
      runId: 'run-xss',
      messageId: 'message-xss'
    },
    { type: 'RUN_FINISHED', threadId, runId: 'run-xss' }
  ];
}

test('hostile model markdown renders inert: no DOM injection, no unsafe hrefs', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  await page.route('**/api/ai/chat/stream', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody(hostileEvents())
    });
  });
  await page.reload();
  await openChat(page);
  await page.getByPlaceholder('Type a message...').fill('show me the manual');
  await page.getByLabel('send-ai-chat-message').click();

  // The answer rendered (benign surrounding text is visible).
  await expect(page.getByText('stays text too')).toBeVisible();
  await expect(page.getByText('to finish.')).toBeVisible();

  // Raw HTML never became DOM: skipHtml drops the tag nodes (their inner
  // text remains visible as harmless prose), no element materializes
  // anywhere, and nothing executed.
  await expect(
    page.getByText('Before window.__xss = true after.')
  ).toBeVisible();
  await expect(page.locator('img[src="x"]')).toHaveCount(0);
  await expect(page.locator('iframe[src="https://evil.example"]')).toHaveCount(
    0
  );
  const executed = await page.evaluate(
    () => (window as unknown as { __xss?: boolean }).__xss === true
  );
  expect(executed).toBe(false);

  // Unsafe and relative hrefs are downgraded to plain text; a real https
  // link keeps its anchor.
  await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);
  await expect(page.locator('a[href="/inert/path"]')).toHaveCount(0);
  await expect(page.locator('a', { hasText: 'here' })).toHaveCount(0);
  await expect(
    page.locator('a[href="https://example.com/manual.pdf"]')
  ).toHaveCount(1);
});
