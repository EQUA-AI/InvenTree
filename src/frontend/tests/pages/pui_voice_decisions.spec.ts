import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
import {
  goldenEvents,
  mockChatFoundation,
  openChat,
  sseBody,
  threadId
} from './aichat_harness.js';

/**
 * Decision-safety browser coverage (voice-UX plan Phase A/B). Grows per phase;
 * every scenario here must stay green (task A10 suite).
 */

test('retired HITL event never renders an approvable card', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await mockChatFoundation(page);
  // A stray legacy HITL_REQUIRED on the stream (nothing emits it today) must
  // be ignored: no approval card, no "waiting for approval" text, and the
  // answer itself still renders.
  const events = [
    ...goldenEvents.slice(0, -1),
    {
      type: 'HITL_REQUIRED',
      threadId,
      runId: 'run-golden',
      action: 'delete_item',
      details: { item_name: 'Pump seal' },
      timeout_seconds: 30
    },
    goldenEvents[goldenEvents.length - 1]
  ];
  await page.route('**/api/ai/chat/stream', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody(events)
    });
  });

  await page.reload();
  await openChat(page);
  await page.getByLabel('select-ai-chat-thread').click();
  await page.getByPlaceholder('Type a message...').fill('Delete the pump seal');
  await page.getByLabel('send-ai-chat-message').click();

  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();
  await expect(page.getByText('Waiting for your approval')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^approve$/i })).toHaveCount(0);
  await expect(page.getByText('Permanently delete')).toHaveCount(0);
});
