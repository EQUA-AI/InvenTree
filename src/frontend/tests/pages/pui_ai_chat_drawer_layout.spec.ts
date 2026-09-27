import { type Page, type Route, expect, test } from '@playwright/test';
/**
 * AI chat drawer layout acceptance (plan packages A/B/E — C1, C2, C3, U1).
 *
 * MOCKED BROWSER RENDERING: real drawer components on the
 * playwright/aichat-drawer.html fixture, wholly mocked auth (no credentials)
 * and Playwright route mocks for every endpoint. Run:
 *
 *   node node_modules/@playwright/test/cli.js test \
 *     --config=playwright.chat.config.ts
 */
import type { VoicePendingDecision } from '../../lib/types/Voice';
import {
  loadDrawerFixture,
  prepareDrawerFixture
} from './ai_drawer_harness.js';
import {
  type ObservedRequest,
  observeRequest,
  sseBody
} from './aichat_harness.js';
import { startVoice } from './voice_harness.js';

// ---------------------------------------------------------------------------
// fixtures

function proposalFixture(overrides: Record<string, unknown> = {}) {
  return {
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
    preview_hash: 'b'.repeat(64),
    reason: 'pump inspection',
    expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    receipt: null,
    failure_code: null,
    ...overrides
  };
}

function inboxItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 'approval-1',
    summary: 'Approve pump seal replacement',
    action_type: 'work_order.hold',
    status: 'pending',
    risk_tier: 2,
    ...overrides
  };
}

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
    spoken_summary: 'Put the work order on hold. Say confirm hold or yes.',
    spoken_summary_hash: ''
  };
}

interface ApprovalSourceOptions {
  proposals?: unknown[];
  inbox?: unknown[];
  resolved?: unknown[];
  /** /api/approvals/count/?status=pending */
  pendingCount?: number;
  /** Non-200 => "count unavailable". */
  countStatus?: number;
}

interface ApprovalSourcesObserved {
  proposalPosts: ObservedRequest[];
  approvalPosts: ObservedRequest[];
  streamPosts: ObservedRequest[];
}

async function mockApprovalSources(
  page: Page,
  options: ApprovalSourceOptions = {}
): Promise<ApprovalSourcesObserved> {
  const observed: ApprovalSourcesObserved = {
    proposalPosts: [],
    approvalPosts: [],
    streamPosts: []
  };
  let confirmed = false;
  await page.route('**/api/aichat/proposals/**', async (route: Route) => {
    const request = route.request();
    if (request.method() === 'GET') {
      // Stateful after a confirm: the list reflects the executed proposal.
      const results = (options.proposals ?? []).map((proposal: any) =>
        confirmed && proposal.id === proposalFixture().id
          ? {
              ...proposal,
              state: 'executed',
              receipt: {
                command: 'hold',
                lifecycle_status: 'on_hold',
                event_id: 5
              }
            }
          : proposal
      );
      await route.fulfill({ json: { results } });
      return;
    }
    observed.proposalPosts.push(await observeRequest(request));
    confirmed = true;
    await route.fulfill({
      json: {
        ...proposalFixture(),
        state: 'executed',
        receipt: { command: 'hold', lifecycle_status: 'on_hold', event_id: 5 }
      }
    });
  });
  await page.route('**/api/approvals/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith('/count/')) {
      if ((options.countStatus ?? 200) !== 200) {
        await route.fulfill({
          status: options.countStatus ?? 500,
          json: { detail: 'count unavailable' }
        });
        return;
      }
      await route.fulfill({ json: { count: options.pendingCount ?? 0 } });
      return;
    }
    if (request.method() !== 'GET') {
      observed.approvalPosts.push(await observeRequest(request));
      await route.fulfill({ json: {} });
      return;
    }
    if (url.pathname.endsWith('/card-package/')) {
      await route.fulfill({
        json: {
          summary: 'Approve pump seal replacement',
          action_type: 'work_order.hold',
          status: 'pending',
          risk_tier: 2,
          current_revision_number: 1,
          review_hash: 'c'.repeat(64),
          payload: {},
          execution_result: null,
          review_sections: [
            { id: 'reason', label: 'Reason', text: 'Seal is missing' }
          ]
        }
      });
      return;
    }
    const status = url.searchParams.get('status') ?? '';
    await route.fulfill({
      json: status.includes('succeeded')
        ? (options.resolved ?? [])
        : (options.inbox ?? [])
    });
  });
  await page.route('**/api/ai/chat/stream', async (route: Route) => {
    observed.streamPosts.push(await observeRequest(route.request()));
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody()
    });
  });
  return observed;
}

function viewportMetrics(page: Page) {
  return page
    .locator('[data-testid="ai-chat-messages-viewport"]')
    .evaluate((el) => ({
      scrollTop: el.scrollTop,
      clientHeight: el.clientHeight,
      scrollHeight: el.scrollHeight
    }));
}

// ---------------------------------------------------------------------------
// C1 — approvals own all approval/proposal lists and review controls

test('C1: proposal lists and approval panels render only in Approvals', async ({
  page
}) => {
  await prepareDrawerFixture(page);
  const observed = await mockApprovalSources(page, {
    proposals: [proposalFixture()],
    inbox: [inboxItem()]
  });
  await loadDrawerFixture(page);

  // Chat: transcript only — no queue, preview or actionable card.
  await expect(page.getByTestId('chat-action-proposals')).toHaveCount(0);
  await expect(page.getByTestId('chat-action-proposal')).toHaveCount(0);
  await expect(page.getByTestId('approval-inbox-panel')).toHaveCount(0);
  await expect(page.getByTestId('voice-decision-card')).toHaveCount(0);

  // History: conversation search/resume only. Mail: mailbox workflow only.
  for (const tab of ['History', 'Mail']) {
    await page.getByRole('tab', { name: tab, exact: true }).click();
    await expect(page.getByTestId('chat-action-proposals')).toHaveCount(0);
    await expect(page.getByTestId('chat-action-proposal')).toHaveCount(0);
    await expect(page.getByTestId('approval-inbox-panel')).toHaveCount(0);
    await expect(page.getByTestId('voice-decision-card')).toHaveCount(0);
  }

  // Approvals: both sources live here and stay actionable.
  await page.getByRole('tab', { name: 'Approvals' }).click();
  await expect(page.getByTestId('chat-action-proposals')).toBeVisible();
  const card = page.getByTestId('chat-action-proposal').first();
  await expect(card).toBeVisible();
  await expect(card).toContainText('WO-000140');
  await expect(page.getByTestId('approval-inbox-panel')).toBeVisible();
  await expect(page.getByTestId('approval-inbox-row')).toHaveCount(1);

  // Merely changing tabs sent no mutation; explicit confirmation does.
  expect(observed.proposalPosts).toHaveLength(0);
  expect(observed.approvalPosts).toHaveLength(0);
  await card.getByTestId('proposal-confirm').click();
  await expect.poll(() => observed.proposalPosts.length).toBe(1);
  expect(observed.proposalPosts[0].body).toMatchObject({
    confirm_phrase: '',
    expected_preview_hash: 'b'.repeat(64)
  });
  await expect(card).toContainText('Executed:');
});

test('C1: resolved actions moved from History into an Approvals Resolved filter', async ({
  page
}) => {
  await prepareDrawerFixture(page);
  await mockApprovalSources(page, {
    inbox: [inboxItem()],
    resolved: [inboxItem({ id: 'approval-done', status: 'succeeded' })]
  });
  await loadDrawerFixture(page);

  await page.getByRole('tab', { name: 'Approvals' }).click();
  await expect(page.getByTestId('approval-inbox-row').first()).toContainText(
    'Approve pump seal replacement'
  );

  // Resolved filter switches the local inbox view.
  await page.getByTestId('approvals-filter-resolved').click();
  await expect(page.getByTestId('approval-inbox-row')).toHaveCount(1);
  await expect(page.getByTestId('approval-inbox-row')).toContainText(
    'succeeded'
  );
  await page.getByTestId('approvals-filter-needs-review').click();
  await expect(page.getByTestId('approval-inbox-row')).toContainText('pending');

  // History keeps no resolved-action history.
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await expect(page.getByTestId('approval-inbox-panel')).toHaveCount(0);
});

test('C1: review inbox is labeled apart from recent proposals with scoped empty states', async ({
  page
}) => {
  // Proposal-only scenario: the recent list has actions, the review inbox
  // does not — its empty state must not claim globally that no actions exist.
  await prepareDrawerFixture(page);
  await mockApprovalSources(page, { proposals: [proposalFixture()] });
  await loadDrawerFixture(page);
  await page.getByRole('tab', { name: 'Approvals' }).click();

  // Recent proposals stay unfiltered above, clearly their own section.
  await expect(page.getByText('Recent action proposals')).toBeVisible();
  await expect(page.getByTestId('chat-action-proposal')).toHaveCount(1);

  // The switch and its inbox are the review inbox, scoped to review requests.
  await expect(page.getByTestId('approval-review-inbox-heading')).toHaveText(
    'Review inbox'
  );
  await expect(
    page.getByText('No approval requests waiting for review')
  ).toBeVisible();
  await expect(page.getByText('No actions waiting for review')).toHaveCount(0);
  await expect(page.getByTestId('chat-action-proposal')).toHaveCount(1);

  // Resolved carries its own scoped empty state; the recent list is untouched.
  await page.getByTestId('approvals-filter-resolved').click();
  await expect(
    page.getByText('No resolved approval requests yet')
  ).toBeVisible();
  await expect(page.getByTestId('chat-action-proposal')).toHaveCount(1);
});

test('C1: switching the review filter resets a selected detail to the new section', async ({
  page
}) => {
  await prepareDrawerFixture(page);
  await mockApprovalSources(page, {
    inbox: [inboxItem()],
    resolved: [inboxItem({ id: 'approval-done', status: 'succeeded' })]
  });
  await loadDrawerFixture(page);
  await page.getByRole('tab', { name: 'Approvals' }).click();

  // Open the pending review detail.
  await page
    .getByTestId('approval-inbox-row')
    .first()
    .getByRole('button')
    .click();
  await expect(page.getByTestId('approval-screen-review')).toBeVisible();

  // Switching sections shows the new section's list, never a stale detail.
  await page.getByTestId('approvals-filter-resolved').click();
  await expect(page.getByTestId('approval-screen-review')).toHaveCount(0);
  await expect(page.getByTestId('approval-inbox-row')).toHaveCount(1);
  await expect(page.getByTestId('approval-inbox-row')).toHaveAttribute(
    'data-source-id',
    'approval-done'
  );

  // Back to Needs review: the pending list again, no stuck detail.
  await page.getByTestId('approvals-filter-needs-review').click();
  await expect(page.getByTestId('approval-screen-review')).toHaveCount(0);
  await expect(page.getByTestId('approval-inbox-row')).toHaveAttribute(
    'data-source-id',
    'approval-1'
  );
});

test('C1: exactly one focused decision card, owned by Approvals and the hands-free surface', async ({
  page
}) => {
  const decision = holdDecision();
  const { voice } = await prepareDrawerFixture(page, {
    voice: { onDecisionRead: () => ({ pending_decision: decision }) }
  });
  await mockApprovalSources(page, {
    inbox: [inboxItem({ id: decision.source_id })]
  });
  await loadDrawerFixture(page);
  await startVoice(page);
  // The pending decision arrives via the shared poller's decision refresh.
  await expect
    .poll(() => voice?.decisionReads.length ?? 0, { timeout: 15_000 })
    .toBeGreaterThan(0);

  // Not in Chat/History/Mail even while a decision waits.
  await expect(page.getByTestId('voice-decision-card')).toHaveCount(0);
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await expect(page.getByTestId('voice-decision-card')).toHaveCount(0);
  await page.getByRole('tab', { name: 'Mail', exact: true }).click();
  await expect(page.getByTestId('voice-decision-card')).toHaveCount(0);

  // Approvals owns the single card.
  await page.getByRole('tab', { name: 'Approvals' }).click();
  const card = page.getByTestId('voice-decision-card');
  await expect(card).toHaveCount(1);
  await expect(card).toContainText(decision.target_label);
  await expect(card).toContainText(decision.spoken_summary);

  // A delegated inbox item must not point at an absent card by position.
  await page
    .getByTestId('approval-inbox-row')
    .first()
    .getByRole('button')
    .click();
  const focusNotice = page.getByTestId('approval-shared-focus');
  await expect(focusNotice).toBeVisible();
  await expect(focusNotice).not.toContainText('above');

  // The hands-free surface takes over while it is open (mutual exclusion).
  await page.evaluate(() => (window as any).__aiDrawerTest.openHandsFree());
  await expect(page.getByTestId('voice-decision-card')).toHaveCount(1);
  await expect(
    page.getByTestId('voice-hands-free').getByTestId('voice-decision-card')
  ).toHaveCount(1);
  await page.evaluate(() => (window as any).__aiDrawerTest.closeHandsFree());
  await expect(page.getByTestId('voice-decision-card')).toHaveCount(1);
});

test('C1: attention indicator instead of a false aggregate count', async ({
  page
}) => {
  // Known actionable proposals light the attention indicator even when the
  // inbox count is zero or unavailable — and no numeric total is claimed.
  await prepareDrawerFixture(page);
  await mockApprovalSources(page, {
    proposals: [proposalFixture()],
    pendingCount: 0
  });
  await loadDrawerFixture(page);
  const indicator = page.getByTestId('approvals-attention-indicator');
  await expect(indicator).toBeVisible();
  await expect(indicator).toHaveAttribute('aria-label', 'Actions need review');
  await expect(page.getByRole('tab', { name: 'Approvals' })).not.toContainText(
    /\d/
  );

  // Review affordance in Chat navigates only; it must not act.
  const affordance = page.getByTestId('review-in-approvals');
  await expect(affordance).toHaveCount(1);
  await affordance.click();
  await expect(page.getByTestId('chat-action-proposals')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Approvals' })).toHaveAttribute(
    'aria-selected',
    'true'
  );
});

test('C1: no attention indicator without known actionable items', async ({
  page
}) => {
  await prepareDrawerFixture(page);
  await mockApprovalSources(page, { proposals: [], pendingCount: 0 });
  await loadDrawerFixture(page);
  await expect(page.getByTestId('approvals-attention-indicator')).toHaveCount(
    0
  );
  await expect(page.getByTestId('review-in-approvals')).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// C2 — scope menu removed, truthful read-only context retained

test('C2: no scope menu; read-only context states confirmed analysis scope', async ({
  page
}) => {
  await prepareDrawerFixture(page);
  await mockApprovalSources(page);
  await loadDrawerFixture(page);

  // The menu, its trigger and its options are gone for every thread type.
  await expect(page.getByLabel('change-ai-chat-scope')).toHaveCount(0);
  await expect(page.getByTestId('scope-option-fleet')).toHaveCount(0);
  await expect(page.getByTestId('scope-option-machine')).toHaveCount(0);

  // Unconfirmed is reported as unconfirmed, not as a machine or fleet scope.
  await expect(page.getByTestId('ai-chat-analysis-context')).toContainText(
    'Analysis context not confirmed'
  );
});

test('C2: machine Ask seeds the scope before the send and collapses the hint', async ({
  page
}) => {
  const { foundation } = await prepareDrawerFixture(page);
  const observed = await mockApprovalSources(page);
  await loadDrawerFixture(page);

  await page.getByTestId('fixture-ask-machine').click();
  await expect(page.getByTestId('ai-chat-routing-hint')).toBeVisible();

  const composer = page.getByPlaceholder('Type a message...');
  await composer.fill('What is wrong?');
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();

  // Scope PUT first (version echo proves ordering), text byte-identical.
  expect(foundation.scopeMutations).toHaveLength(1);
  expect(foundation.scopeMutations[0].body?.scope).toMatchObject({
    mode: 'explicit_assets',
    machine_ids: [7],
    display_label: 'Pump A'
  });
  expect(observed.streamPosts[0].body?.message).toBe('What is wrong?');
  expect(observed.streamPosts[0].body?.expected_scope_version).toBe(1);

  // The confirmed scope is shown; the redundant hint collapsed.
  await expect(page.getByTestId('ai-chat-analysis-context')).toContainText(
    'Analyzing: Pump A'
  );
  await expect(page.getByTestId('ai-chat-routing-hint')).toHaveCount(0);
});

test('C2: dismissing the hint never clears a confirmed scope', async ({
  page
}) => {
  const { foundation } = await prepareDrawerFixture(page);
  await mockApprovalSources(page);
  await loadDrawerFixture(page);

  // Unconfirmed scope + hint: dismissal hides the chip only.
  await page.getByTestId('fixture-ask-machine').click();
  await expect(page.getByTestId('ai-chat-routing-hint')).toBeVisible();
  await page.getByLabel('dismiss-routing-hint').click();
  await expect(page.getByTestId('ai-chat-routing-hint')).toHaveCount(0);
  expect(foundation.scopeMutations).toHaveLength(0);
  await expect(page.getByTestId('ai-chat-analysis-context')).toContainText(
    'Analysis context not confirmed'
  );

  // Once the server confirmed the same machine the hint is redundant.
  foundation.scope.mode = 'explicit_assets';
  foundation.scope.version = 1;
  foundation.scope.machineIds = [7];
  foundation.scope.displayLabel = 'Pump A';
  await page.reload();
  await page.getByLabel('open-ai-chat').click();
  await page.getByTestId('fixture-ask-machine').click();
  await expect(page.getByTestId('ai-chat-routing-hint')).toHaveCount(0);
  await expect(page.getByTestId('ai-chat-analysis-context')).toContainText(
    'Analyzing: Pump A'
  );
});

// ---------------------------------------------------------------------------
// C3 — microphone in the composer, active controls across tabs

test('C3: mic starts inside the composer and never submits typed text', async ({
  page
}) => {
  await prepareDrawerFixture(page);
  await mockApprovalSources(page);
  await loadDrawerFixture(page);

  // The start action lives inside the composer, adjacent to text entry.
  const mic = page.getByTestId('ai-chat-composer').getByTestId('voice-start');
  await expect(mic).toBeVisible();
  await expect(page.getByTestId('voice-start')).toHaveCount(1);
  await expect(mic).toHaveAttribute('aria-label', 'Start voice session');

  // Clicking the mic starts a voice session — it must not send the draft.
  await page.getByPlaceholder('Type a message...').fill('do not send me');
  await mic.click();
  await expect(
    page.getByRole('dialog', { name: 'Start a voice session' })
  ).toBeVisible();
  await expect(page.getByPlaceholder('Type a message...')).toHaveValue(
    'do not send me'
  );
});

test('C3: active voice strip stays reachable on every tab and close cleans up', async ({
  page
}) => {
  const { voice } = await prepareDrawerFixture(page);
  await mockApprovalSources(page);
  await loadDrawerFixture(page);
  await startVoice(page);

  const strip = page.getByTestId('ai-chat-voice-active');
  await expect(strip).toBeVisible();
  await expect(strip.getByTestId('voice-mute')).toBeVisible();
  await expect(strip.getByTestId('voice-end')).toBeVisible();

  await page.getByPlaceholder('Type a message...').fill('draft text');
  for (const tab of ['Approvals', 'History', 'Mail']) {
    await page.getByRole('tab', { name: tab, exact: true }).click();
    await expect(strip).toBeVisible();
    await expect(strip.getByTestId('voice-end')).toBeVisible();
    await expect(strip.getByTestId('voice-mute')).toBeVisible();
  }
  // Switching tabs mounted no second session.
  expect(voice?.sessionCreates ?? []).toHaveLength(1);
  await page.getByRole('tab', { name: 'Chat', exact: true }).click();
  await expect(page.getByPlaceholder('Type a message...')).toHaveValue(
    'draft text'
  );

  // Closing the drawer ends voice (current explicit close policy).
  await page.getByLabel('close-ai-chat').click();
  await expect.poll(() => voice?.sessionEnded).toBe(true);
  await expect(page.getByTestId('voice-minimized-indicator')).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// U1 — Ask intent, scroll follow, IME, suggestions, input label

test('U1: machine Ask opens the Chat tab once and manual tab choice sticks', async ({
  page
}) => {
  await prepareDrawerFixture(page, { tab: 'approvals', voice: false });
  await mockApprovalSources(page);
  await loadDrawerFixture(page, { open: false });

  await page.getByTestId('fixture-ask-machine').click();
  await expect(
    page.getByRole('tab', { name: 'Chat', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('ai-chat-routing-hint')).toBeVisible();

  // The intent is consumed once: switching back to Approvals stays put.
  await page.getByRole('tab', { name: 'Approvals' }).click();
  await page.waitForTimeout(400);
  await expect(page.getByRole('tab', { name: 'Approvals' })).toHaveAttribute(
    'aria-selected',
    'true'
  );
});

test('U1: streaming follows only a reader near the bottom; jump to latest otherwise', async ({
  page
}) => {
  const messages = Array.from({ length: 20 }, (_, index) => ({
    id: `m${index}`,
    role: index % 2 === 0 ? 'assistant' : 'user',
    content: `Scroll fixture message ${index + 1}`,
    timestamp: '2026-07-15T00:01:00Z'
  }));
  await prepareDrawerFixture(page, {
    voice: false,
    foundation: { threadDetailMessages: messages }
  });
  await mockApprovalSources(page);
  await loadDrawerFixture(page);
  await expect(
    page.getByText('Scroll fixture message 20', { exact: true })
  ).toBeVisible();

  // Scroll up, then a new turn arrives: no yank, an explicit jump control.
  await page
    .locator('[data-testid="ai-chat-messages-viewport"]')
    .evaluate((el) => {
      el.scrollTop = 0;
      el.dispatchEvent(new Event('scroll'));
    });
  await page.getByPlaceholder('Type a message...').fill('while scrolled up');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('ai-chat-jump-latest')).toBeVisible();
  const scrolled = await viewportMetrics(page);
  expect(scrolled.scrollTop).toBeLessThan(
    scrolled.scrollHeight - scrolled.clientHeight - 100
  );

  // Jump to latest re-pins the reader and hides the control.
  await page.getByTestId('ai-chat-jump-latest').click();
  await expect(page.getByTestId('ai-chat-jump-latest')).toHaveCount(0);
  const pinned = await viewportMetrics(page);
  expect(
    pinned.scrollHeight - pinned.scrollTop - pinned.clientHeight
  ).toBeLessThan(60);

  // A reader already at the bottom keeps following without extra controls.
  await page.getByPlaceholder('Type a message...').fill('while pinned');
  await page.keyboard.press('Enter');
  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toHaveCount(2);
  await expect(page.getByTestId('ai-chat-jump-latest')).toHaveCount(0);
  const followed = await viewportMetrics(page);
  expect(
    followed.scrollHeight - followed.scrollTop - followed.clientHeight
  ).toBeLessThan(60);
});

test('U1: Enter is IME-safe, Shift+Enter newlines, and the input has a label', async ({
  page
}) => {
  await prepareDrawerFixture(page, { voice: false });
  const observed = await mockApprovalSources(page);
  await loadDrawerFixture(page);

  const composer = page.getByLabel('Message', { exact: true });
  await expect(composer).toBeVisible();
  await composer.fill('ime draft');

  // Enter mid-composition must not send.
  await composer.evaluate((el) => {
    el.dispatchEvent(
      new KeyboardEvent('keydown', {
        key: 'Enter',
        bubbles: true,
        cancelable: true,
        isComposing: true
      })
    );
  });
  expect(observed.streamPosts).toHaveLength(0);
  await expect(composer).toHaveValue('ime draft');

  // Legacy keyCode-229 composition signal is also guarded.
  await composer.evaluate((el) => {
    el.dispatchEvent(
      new KeyboardEvent('keydown', {
        key: 'Enter',
        bubbles: true,
        cancelable: true,
        keyCode: 229
      })
    );
  });
  expect(observed.streamPosts).toHaveLength(0);

  // Shift+Enter inserts a newline, plain Enter sends.
  await composer.click();
  await page.keyboard.press('Shift+Enter');
  await expect(composer).toHaveValue('ime draft\n');
  expect(observed.streamPosts).toHaveLength(0);
  await page.keyboard.press('Enter');
  await expect.poll(() => observed.streamPosts.length).toBe(1);
  expect(observed.streamPosts[0].body?.message).toBe('ime draft\n');
});

test('U1: empty-state suggestions are semantic buttons and adapt to a machine hint', async ({
  page
}) => {
  await prepareDrawerFixture(page, {
    voice: false,
    foundation: { threadDetailMessages: [] }
  });
  const observed = await mockApprovalSources(page);
  await loadDrawerFixture(page);

  const suggestions = page.getByTestId('ai-chat-suggestion');
  await expect(suggestions).toHaveCount(3);
  await expect(
    page.getByRole('button', { name: 'Search parts' })
  ).toBeVisible();
  await page.getByRole('button', { name: 'Search parts' }).click();
  await expect.poll(() => observed.streamPosts.length).toBe(1);
  expect(observed.streamPosts[0].body?.message).toBe(
    'Search for parts in inventory'
  );

  // Machine hint → machine-relevant prompts.
  await page.reload();
  await page.getByLabel('open-ai-chat').click();
  await page.getByTestId('fixture-ask-machine').click();
  await expect(
    page.getByRole('button', { name: 'Machine status' })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Maintenance history' })
  ).toBeVisible();
});

test('U1: context line shows a friendly page label, never the raw path', async ({
  page
}) => {
  await prepareDrawerFixture(page, { voice: false });
  await mockApprovalSources(page);
  await loadDrawerFixture(page, { route: '/machines/index/sites/' });

  const context = page.getByTestId('assistant-page-context');
  await expect(context).toBeVisible();
  await expect(context).toContainText('Machines');
  await expect(context).not.toContainText('/machines/');
});
