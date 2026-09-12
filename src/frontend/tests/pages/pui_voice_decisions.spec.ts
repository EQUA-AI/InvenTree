import type { VoicePendingDecision } from '../../lib/types/Voice';
import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
import {
  goldenEvents,
  mockChatFoundation,
  openChat,
  sseBody,
  threadId
} from './aichat_harness.js';
import {
  emitTranscript,
  installVoiceMocks,
  mockSessionId,
  mockThreadId
} from './voice_harness.js';

/**
 * Decision-safety browser coverage (voice-UX plan Phase A/B). Grows per phase;
 * every scenario here must stay green (task A10 suite).
 */

function holdDecision(
  id = 'decision-one',
  target = 'WO-000104 Pump service'
): VoicePendingDecision {
  return {
    decision_id: id,
    source_id: `proposal-${id}`,
    revision: 3,
    sequence: 1,
    kind: 'action',
    state: 'presented',
    target_label: target,
    sections: [
      { id: 'reason', label: 'Reason', text: 'replacement seal is missing' }
    ],
    required_review_sections: [],
    allowed_responses: ['confirm hold', 'yes', 'change that', 'cancel'],
    required_phrase: null,
    locale: 'en-US',
    voice_eligible: true,
    voice_ineligible_reason: null,
    preview_hash: 'a'.repeat(64),
    expires_at: new Date(Date.now() + 120000).toISOString(),
    utterance_id: null,
    delivery_state: 'pending',
    review_acknowledged: false,
    operation_id: null,
    execution_state: null,
    receipt_ref: null,
    spoken_summary:
      'Put the work order on hold because the replacement seal is missing. Say confirm hold or yes.',
    spoken_summary_hash: ''
  };
}

test('one decision card and voice controls remain available across all tabs and minimized state', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await mockChatFoundation(page);
  const decision = holdDecision();
  const voice = await installVoiceMocks(page, {
    onDecisionRead: () => ({ pending_decision: decision })
  });
  await page.reload();
  await openChat(page);
  await page.getByTestId('voice-start').click();
  for (const tab of ['Chat', 'Approvals', 'History']) {
    await page.getByRole('tab', { name: tab, exact: true }).click();
    await expect(page.getByTestId('voice-decision-card')).toHaveCount(1);
    await expect(page.getByTestId('voice-decision-card')).toContainText(
      decision.target_label
    );
    await expect(page.getByTestId('voice-end')).toBeVisible();
  }
  await expect(page.getByTestId('voice-decision-spoken-summary')).toHaveText(
    decision.spoken_summary
  );
  await page.getByLabel('close-ai-chat').click();
  await expect(page.getByTestId('voice-minimized-indicator')).toContainText(
    decision.target_label
  );
  expect(voice.sessionEnded).toBe(false);
  await page
    .getByTestId('voice-minimized-indicator')
    .getByRole('button', { name: 'End voice' })
    .click();
  await expect.poll(() => voice.sessionEnded).toBe(true);
});

test('turns carry decision_context and correction re-presents a fresh target', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await mockChatFoundation(page);
  let decision = holdDecision();
  const first = { ...decision };
  const voice = await installVoiceMocks(page, {
    onDecisionRead: () => ({ pending_decision: decision }),
    onTurn: () => {
      decision = holdDecision('decision-two', 'WO-000140 Pump service');
      return {
        session_id: mockSessionId,
        thread_id: mockThreadId,
        turn_id: 'turn-correction',
        message: decision.spoken_summary,
        response_state: 'complete',
        workflow_used: 'voice_decision',
        replayed: false,
        spoken: null,
        pending_question: null,
        pending_decision: decision,
        decision_event: null
      };
    }
  });
  await page.reload();
  await openChat(page);
  await page.getByTestId('voice-start').click();
  await expect(page.getByTestId('voice-decision-card')).toBeVisible();
  await emitTranscript(page, {
    text: 'no, I meant 140',
    itemId: 'correct-140'
  });
  await expect.poll(() => voice.turns.length).toBe(1);
  expect(voice.turns[0].body?.decision_context).toEqual({
    decision_id: first.decision_id,
    sequence: first.sequence,
    revision: first.revision,
    preview_hash: first.preview_hash
  });
  await expect(page.getByTestId('voice-decision-card')).toContainText(
    'WO-000140'
  );
  await expect(page.getByTestId('voice-pending-transcript')).toHaveCount(0);
});

test('touch confirmation sends hash and revision and unknown result stays unverified', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await mockChatFoundation(page);
  let decision = holdDecision();
  const voice = await installVoiceMocks(page, {
    onDecisionRead: () => ({ pending_decision: decision }),
    onDecisionAction: () => {
      decision = {
        ...decision,
        sequence: 2,
        state: 'resolved',
        execution_state: 'unknown'
      };
      return { pending_decision: decision };
    }
  });
  await page.reload();
  await openChat(page);
  await page.getByTestId('voice-start').click();
  await page.getByTestId('voice-decision-confirm').click();
  await expect.poll(() => voice.decisionActions.length).toBe(1);
  expect(voice.decisionActions[0].body).toMatchObject({
    decision_id: 'decision-one',
    revision: 3,
    preview_hash: 'a'.repeat(64),
    confirm_phrase: 'confirm hold'
  });
  await expect(page.getByTestId('voice-decision-outcome')).toContainText(
    'Result not verified'
  );
  await expect(
    page
      .getByTestId('voice-decision-card')
      .getByText('Change recorded', { exact: true })
  ).toHaveCount(0);
});

test('metadata-free Azure read-back reports bound delivery without confirming an action', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await mockChatFoundation(page);
  let decision: VoicePendingDecision | null = null;
  const voice = await installVoiceMocks(page, {
    onDecisionRead: () => ({ pending_decision: decision }),
    onTurn: () => {
      decision = {
        ...holdDecision(),
        spoken_summary: 'Confirm hold.',
        utterance_id: 'exact-utterance',
        spoken_summary_hash: 's'.repeat(64),
        delivery_state: 'requested'
      };
      return {
        session_id: mockSessionId,
        thread_id: mockThreadId,
        turn_id: 'turn-exact',
        message: decision.spoken_summary,
        response_state: 'complete',
        workflow_used: 'voice_decision',
        replayed: false,
        pending_question: null,
        pending_decision: decision,
        decision_event: null,
        spoken: {
          utterance_id: decision.utterance_id,
          spoken_summary: decision.spoken_summary,
          spoken_summary_hash: decision.spoken_summary_hash,
          playback_state: 'requested'
        }
      };
    },
    onDecisionAction: (action) => {
      decision = {
        ...decision!,
        sequence: decision!.sequence + 1,
        delivery_state: action === 'playback-started' ? 'playing' : 'done'
      };
      return { pending_decision: decision };
    }
  });
  await page.reload();
  await openChat(page);
  await page.getByTestId('voice-start').click();
  await emitTranscript(page, {
    text: 'Put the work order on hold',
    itemId: 'proposal'
  });
  await expect(page.getByTestId('voice-decision-card')).toBeVisible();
  await page.evaluate(() => {
    const mock = (window as any).__voiceMock;
    const emit = mock.emit.bind(mock);
    emit('response.created', {
      response: { id: 'azure-response', status: 'in_progress' }
    });
    emit('response.audio_transcript.delta', {
      response_id: 'azure-response',
      delta: 'Confirm hold.'
    });
    emit('response.audio_transcript.done', {
      response_id: 'azure-response',
      transcript: 'Confirm hold.'
    });
    emit('response.audio.done', { response_id: 'azure-response' });
    emit('output_audio_buffer.stopped', {});
    emit('response.done', {
      response: { id: 'azure-response', status: 'completed' }
    });
  });
  await expect
    .poll(() => voice.decisionActions.length, { timeout: 8000 })
    .toBe(2);
  expect(
    voice.decisionActions.map((entry) => entry.url.split('/').pop())
  ).toEqual(['playback-started', 'playback-completed']);
  for (const entry of voice.decisionActions) {
    expect(entry.body).toMatchObject({
      utterance_id: 'exact-utterance',
      spoken_summary_hash: 's'.repeat(64),
      confirm_phrase: ''
    });
  }
  expect(voice.turns).toHaveLength(1);
});

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
