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

test('HTTP 200 with business failure shows no success', async ({ browser }) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await mockChatFoundation(page);
  let confirmed = false;
  let proposalReads = 0;
  const proposal = {
    id: 'a1b2c3d4-0000-4000-8000-000000000001',
    action_type: 'work_order.hold',
    state: 'proposed',
    work_order_id: 140,
    target_version: 3,
    intent: { reason: 'pump inspection' },
    preview: {
      action: 'work_order.hold',
      reference: 'WO-000140',
      title: 'Pump inspection',
      current_status: 'in_progress',
      resulting_status: 'on_hold',
      warning: 'This does not change any safety status.'
    },
    reason: 'pump inspection',
    expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    receipt: null,
    failure_code: null
  };
  await page.route('**/api/aichat/proposals/**', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    proposalReads += 1;
    await route.fulfill({
      json: {
        results: confirmed
          ? [
              {
                ...proposal,
                state: 'failed',
                failure_code: 'PROPOSAL_REVALIDATION_FAILED'
              }
            ]
          : [proposal]
      }
    });
  });
  await page.route('**/api/aichat/proposals/*/confirm/', async (route) => {
    confirmed = true;
    // Transport success, business failure: the record changed under the
    // preview. The UI must not present this as done.
    await route.fulfill({
      status: 200,
      json: {
        ...proposal,
        state: 'failed',
        failure_code: 'PROPOSAL_REVALIDATION_FAILED'
      }
    });
  });

  await page.reload();
  await openChat(page);
  // The proposals list lives on the chat tab since A7 (and on the approvals
  // tab); select the chat tab explicitly -- the cached login may restore
  // another tab -- and wait for the list to have fetched.
  await page.getByRole('tab', { name: 'Chat', exact: true }).click();
  await expect.poll(() => proposalReads).toBeGreaterThanOrEqual(1);
  const card = page.getByTestId('chat-action-proposal').first();
  await expect(card).toBeVisible();
  await card.getByTestId('proposal-confirm').click();

  await expect(page.getByTestId('proposal-error').first()).toContainText(
    'PROPOSAL_REVALIDATION_FAILED'
  );
  await expect(page.getByText(/^Executed:/)).toHaveCount(0);
  await expect(page.getByText('Not applied: ')).toHaveCount(0);
});
