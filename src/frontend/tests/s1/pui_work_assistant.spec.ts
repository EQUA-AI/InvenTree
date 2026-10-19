/**
 * P02/P33: asking the one existing assistant about one Work order.
 *
 * The production layout (real Header and drawer) around the real Work page,
 * with every backend response mocked. The Work page names the Work and the
 * version it shows; the drawer must have the server acknowledge exactly that
 * before anything is sent, send only the signed receipt, and label both
 * sides of the exchange with a link back to the panel the question came
 * from. A refusal, a missing capability or a mismatched acknowledgement
 * sends nothing and keeps the draft.
 */
import { type Page, type Route, expect, test } from '@playwright/test';

import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';

import { prepareDrawerFixture } from '../pages/ai_drawer_harness';
import { openChat, sseBody } from '../pages/aichat_harness';
import { nativeBaseline } from '../s0/guards';
import { workOrder } from '../s0/workFixtures';
import { mockWorkList } from './workListFixtures';

nativeBaseline();

const WORK_LABEL = 'WO-73 · Synthetic job 73';

interface Observed {
  acknowledgements: Record<string, unknown>[];
  ackThreads: string[];
  sends: Record<string, unknown>[];
}

type AckReply = 'confirm' | 'refuse' | 'another-version';

async function mockWorkAssistant(
  page: Page,
  options: {
    capable?: boolean;
    reply?: () => AckReply;
    overview?: WorkOrderOverview;
  } = {}
): Promise<Observed> {
  const observed: Observed = {
    acknowledgements: [],
    ackThreads: [],
    sends: []
  };
  await mockWorkList(page);
  // What the production drawer reads in the background once it is open.
  await page.route('**/api/approvals/count/**', (route) =>
    route.fulfill({ json: { count: 0 } })
  );
  await page.route('**/api/aichat/proposals/**', (route) =>
    route.fulfill({ json: { results: [] } })
  );
  // A UI library stylesheet names a web font on its CDN; nothing external
  // is fetched in this harness.
  await page.route('https://cdn.svar.dev/**', (route) =>
    route.fulfill({ status: 404, body: '' })
  );
  await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
    route.fulfill({ json: options.overview ?? workOrder() })
  );
  // The native Closeout panel's governed reads, as the Work-panel suite
  // serves them.
  await page.route('**/api/tasks/work-orders/73/**', (route) =>
    route.fulfill({
      json: route.request().url().includes('/readiness/')
        ? {
            ready: false,
            blockers: [
              {
                code: 'SYNTHETIC_BLOCKER',
                message: 'Synthetic missing verification',
                blocking: true
              }
            ],
            warnings: [],
            evaluated_at: '2026-10-18T12:00:00Z',
            lifecycle_version: 7,
            policy_version: 1,
            snapshot_hash: 'a'.repeat(64)
          }
        : []
    })
  );
  await page.route(/\/api\/ai\/threads(?:\?|$)/, (route) =>
    route.fulfill({
      json: {
        threads: [],
        shared_threads: [],
        has_more: false,
        capabilities: {
          thread_scope: true,
          ...(options.capable === false ? {} : { work_focus: true })
        }
      }
    })
  );
  await page.route('**/api/ai/threads/*/work-focus', async (route: Route) => {
    const request = route.request();
    const thread = decodeURIComponent(
      new URL(request.url()).pathname.split('/').at(-2) ?? ''
    );
    observed.ackThreads.push(thread);
    observed.acknowledgements.push(request.postDataJSON());
    const reply = options.reply?.() ?? 'confirm';
    if (reply === 'refuse') {
      await route.fulfill({
        status: 400,
        json: { detail: 'work_focus_rejected' }
      });
      return;
    }
    await route.fulfill({
      json: {
        schema_version: 1,
        thread_id: thread,
        focus: {
          schema_version: 1,
          kind: 'work_order',
          work_order_id: 73,
          lifecycle_version: reply === 'another-version' ? 8 : 7,
          machine_id: 18
        },
        receipt: 'signed-receipt-73',
        expires_in: 300
      }
    });
  });
  await page.route('**/api/ai/chat/stream', async (route: Route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    observed.sends.push(body);
    const thread = String(body.thread_id);
    await route.fulfill({
      contentType: 'text/event-stream',
      body: sseBody([
        { type: 'RUN_STARTED', threadId: thread, runId: 'run-work' },
        {
          type: 'TEXT_MESSAGE_START',
          threadId: thread,
          runId: 'run-work',
          messageId: 'message-work',
          role: 'assistant'
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          threadId: thread,
          runId: 'run-work',
          messageId: 'message-work',
          delta: 'Two parts are still to be issued for WO-73.'
        },
        {
          type: 'TEXT_MESSAGE_END',
          threadId: thread,
          runId: 'run-work',
          messageId: 'message-work'
        },
        { type: 'RUN_FINISHED', threadId: thread, runId: 'run-work' }
      ])
    });
  });
  return observed;
}

async function openWork(page: Page, panel = 'parts', scheme = 'light') {
  const params = new URLSearchParams({
    surface: 'maintenance-shell',
    sprint: 's1',
    role: 'viewer',
    work_access: 'reader',
    scheme,
    entry: `/maintenance/work-orders/73/?work_panel=${panel}`
  });
  await page.goto(`/playwright/s0-native.html?${params}`);
}

async function askFromWork(page: Page, panel = 'parts', scheme = 'light') {
  await openWork(page, panel, scheme);
  await page.getByRole('button', { name: 'Ask about this work' }).click();
  await expect(page.getByTestId('ai-chat-work-focus')).toContainText(
    WORK_LABEL
  );
  return page.getByRole('textbox', { name: 'Message', exact: true });
}

async function expectHeld(page: Page, observed: Observed, draft: string) {
  await expect(
    page.getByRole('alert').filter({ hasText: 'Work order not confirmed' })
  ).toBeVisible();
  await expect(
    page.getByRole('textbox', { name: 'Message', exact: true })
  ).toHaveValue(draft);
  await expect(page.getByTestId('ai-chat-work-focus')).toBeVisible();
  expect(observed.sends).toHaveLength(0);
}

test('a question from a Work panel is acknowledged exactly, sent with its receipt, and links back to that panel', async ({
  page
}, testInfo) => {
  const observed = await mockWorkAssistant(page);
  const composer = await askFromWork(page, 'parts');
  await composer.fill('Which parts are still outstanding?');
  await page.getByLabel('send-ai-chat-message').click();

  await expect(
    page.getByText('Two parts are still to be issued for WO-73.', {
      exact: true
    })
  ).toBeVisible();
  // The server was asked about this Work at the version the page showed.
  expect(observed.acknowledgements).toEqual([
    { work_order_id: 73, expected_version: 7 }
  ]);
  // Only the receipt goes with the message: no ids, label or page context.
  expect(observed.sends).toHaveLength(1);
  const sent = observed.sends[0];
  expect(sent.thread_id).toBe(observed.ackThreads[0]);
  expect(sent.work_focus_receipt).toBe('signed-receipt-73');
  expect(sent.message).toBe('Which parts are still outstanding?');
  expect(sent).not.toHaveProperty('context');
  expect(JSON.stringify(sent)).not.toContain(WORK_LABEL);

  // The launch is spent; both sides of the exchange name the Work and link
  // back to the panel the question was asked from.
  await expect(page.getByTestId('ai-chat-work-focus')).toHaveCount(0);
  const chips = page.getByTestId('ai-chat-message-work-focus');
  await expect(chips).toHaveCount(2);
  for (const link of await chips.getByRole('link').all()) {
    await expect(link).toHaveAttribute(
      'href',
      '/maintenance/work-orders/73?work_panel=parts'
    );
    await expect(link).toHaveAccessibleName(`Open work order: ${WORK_LABEL}`);
  }
  await page.screenshot({
    path: testInfo.outputPath('work-assistant-answer.png'),
    animations: 'disabled'
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth)
  ).toBeLessThanOrEqual(page.viewportSize()!.width);
});

test('a refused acknowledgement sends nothing, keeps the draft, and the Work can be removed explicitly', async ({
  page
}, testInfo) => {
  let reply: AckReply = 'refuse';
  const observed = await mockWorkAssistant(page, { reply: () => reply });
  const composer = await askFromWork(page, 'verify');
  await composer.fill('Is verification complete for this work?');
  await page.getByLabel('send-ai-chat-message').click();
  await expectHeld(page, observed, 'Is verification complete for this work?');
  expect(observed.acknowledgements).toHaveLength(1);
  await page.screenshot({
    path: testInfo.outputPath('work-assistant-refused.png'),
    animations: 'disabled'
  });

  // Retrying asks again; it is still the user's same question.
  reply = 'confirm';
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByText('Two parts are still to be issued for WO-73.', {
      exact: true
    })
  ).toBeVisible();
  expect(observed.acknowledgements).toHaveLength(2);
  expect(observed.sends).toHaveLength(1);
  expect(observed.sends[0].work_focus_receipt).toBe('signed-receipt-73');
});

test('removing a refused Work is the only way to send without it', async ({
  page
}) => {
  const observed = await mockWorkAssistant(page, { reply: () => 'refuse' });
  const composer = await askFromWork(page);
  await composer.fill('What is the next step?');
  await page.getByLabel('send-ai-chat-message').click();
  await expectHeld(page, observed, 'What is the next step?');

  await page.getByRole('button', { name: 'Remove work order' }).click();
  await expect(page.getByTestId('ai-chat-work-focus')).toHaveCount(0);
  await expect(
    page.getByRole('alert').filter({ hasText: 'Work order not confirmed' })
  ).toHaveCount(0);
  await expect(composer).toHaveValue('What is the next step?');
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByText('Two parts are still to be issued for WO-73.', {
      exact: true
    })
  ).toBeVisible();
  expect(observed.acknowledgements).toHaveLength(1);
  expect(observed.sends).toHaveLength(1);
  expect(observed.sends[0]).not.toHaveProperty('work_focus_receipt');
  await expect(page.getByTestId('ai-chat-message-work-focus')).toHaveCount(0);
});

test('without the advertised capability nothing is acknowledged or sent', async ({
  page
}) => {
  const observed = await mockWorkAssistant(page, { capable: false });
  const composer = await askFromWork(page);
  await composer.fill('Summarise this work.');
  await page.getByLabel('send-ai-chat-message').click();
  await expectHeld(page, observed, 'Summarise this work.');
  await expect(
    page.getByText('this assistant cannot confirm a work order yet', {
      exact: false
    })
  ).toBeVisible();
  expect(observed.acknowledgements).toHaveLength(0);
});

test('a 200 naming another version is not an acknowledgement', async ({
  page
}) => {
  const observed = await mockWorkAssistant(page, {
    reply: () => 'another-version'
  });
  const composer = await askFromWork(page);
  await composer.fill('Has this work changed?');
  await page.getByLabel('send-ai-chat-message').click();
  await expectHeld(page, observed, 'Has this work changed?');
  expect(observed.acknowledgements).toHaveLength(1);
});

test('repeated activation during acknowledgement asks once and sends once', async ({
  page
}) => {
  const observed = await mockWorkAssistant(page);
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let attempts = 0;
  await page.route('**/api/ai/threads/*/work-focus', async (route) => {
    attempts += 1;
    await held;
    await route.fallback();
  });
  const composer = await askFromWork(page);
  await composer.fill('Only once, please.');
  try {
    await page
      .getByLabel('send-ai-chat-message')
      .evaluate((element: HTMLButtonElement) => {
        element.click();
        element.click();
      });
    await expect.poll(() => attempts).toBe(1);
    // The composer and attachments hold still while the Work is confirmed.
    await expect(composer).toBeDisabled();
    await expect(page.getByLabel('attach-ai-chat-file')).toBeDisabled();
    expect(observed.sends).toHaveLength(0);
  } finally {
    release();
  }
  await expect(
    page.getByText('Two parts are still to be issued for WO-73.', {
      exact: true
    })
  ).toBeVisible();
  expect(attempts).toBe(1);
  expect(observed.acknowledgements).toHaveLength(1);
  expect(observed.sends).toHaveLength(1);
});

test('following the answer back returns to the same panel with the unsaved native draft', async ({
  page
}, testInfo) => {
  const draft = 'Synthetic unsaved closeout draft — keep while asking';
  const observed = await mockWorkAssistant(page, {
    overview: { ...workOrder(), canonical_commands_enabled: true }
  });
  await openWork(page, 'closeout');
  const narrative = page.getByRole('textbox', {
    name: 'What did you do?',
    exact: true
  });
  await narrative.fill(draft);
  await page.getByRole('button', { name: 'Ask about this work' }).click();
  const composer = page.getByRole('textbox', { name: 'Message', exact: true });
  await composer.fill('What is still needed before I can close this out?');
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByText('Two parts are still to be issued for WO-73.', {
      exact: true
    })
  ).toBeVisible();
  expect(observed.sends).toHaveLength(1);

  await page
    .getByTestId('ai-chat-message-work-focus')
    .last()
    .getByRole('link')
    .click();
  await expect(page.getByTestId('destination')).toHaveText(
    '/maintenance/work-orders/73?work_panel=closeout'
  );
  const tabs = page.getByRole('tablist', {
    name: 'Work workflow',
    exact: true
  });
  await expect(
    tabs.getByRole('tab', { name: 'Closeout', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  await expect(narrative).toHaveValue(draft);
  if ((page.viewportSize()?.width ?? 0) < 768) {
    // On a phone the drawer gets out of the way of the page it opened.
    await expect(
      page.getByRole('textbox', { name: 'Message', exact: true })
    ).toBeHidden();
  }
  await page.screenshot({
    path: testInfo.outputPath('work-assistant-returned.png'),
    animations: 'disabled'
  });
});

test('the launcher fits the Work header and the refusal reads in the dark scheme', async ({
  page
}, testInfo) => {
  const observed = await mockWorkAssistant(page, { reply: () => 'refuse' });
  await openWork(page, 'work', 'dark');
  const launcher = page.getByRole('button', { name: 'Ask about this work' });
  await expect(launcher).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth)
  ).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.screenshot({
    path: testInfo.outputPath('work-assistant-launcher-dark.png'),
    animations: 'disabled'
  });
  // Keyboard: the launcher is a native button and the drawer takes focus.
  await launcher.focus();
  await page.keyboard.press('Enter');
  const composer = page.getByRole('textbox', { name: 'Message', exact: true });
  await expect(composer).toBeFocused();
  await composer.fill('Dark scheme refusal check.');
  await page.keyboard.press('Enter');
  await expectHeld(page, observed, 'Dark scheme refusal check.');
  await page.screenshot({
    path: testInfo.outputPath('work-assistant-refused-dark.png'),
    animations: 'disabled'
  });
});

test.describe('reload', () => {
  const identity = (id: number) => ({
    schema_version: 1,
    kind: 'work_order',
    work_order_id: id,
    lifecycle_version: 7,
    machine_id: 18
  });

  test('a reloaded answer says how its Work stands now and keeps only valid links', async ({
    page
  }, testInfo) => {
    await prepareDrawerFixture(page, {
      voice: false,
      foundation: {
        threadDetailMessages: [
          {
            id: 'current-answer',
            role: 'assistant',
            content: 'Saved answer, Work unchanged',
            timestamp: '2026-10-19T00:01:00Z',
            work_focus: identity(73),
            work_focus_state: 'current'
          },
          {
            id: 'changed-answer',
            role: 'assistant',
            content: 'Saved answer, Work changed since',
            timestamp: '2026-10-19T00:02:00Z',
            work_focus: identity(74),
            work_focus_state: 'changed'
          },
          {
            id: 'withdrawn-answer',
            role: 'assistant',
            content: 'Saved answer, Work no longer visible',
            timestamp: '2026-10-19T00:03:00Z',
            work_focus: identity(75),
            work_focus_state: 'unavailable'
          },
          {
            id: 'unknown-state-answer',
            role: 'assistant',
            content: 'Saved answer from a newer server',
            timestamp: '2026-10-19T00:04:00Z',
            work_focus: identity(76),
            work_focus_state: 'superseded'
          },
          {
            id: 'malformed-answer',
            role: 'assistant',
            content: 'Saved answer with a malformed focus',
            timestamp: '2026-10-19T00:05:00Z',
            work_focus: { work_order_id: 77, boundary_hash: 'b' },
            work_focus_state: 'current'
          }
        ]
      }
    });
    await page.goto('/playwright/aichat-drawer.html?actor=reader');
    await openChat(page);
    await expect(
      page.getByText('Saved answer with a malformed focus', { exact: true })
    ).toBeVisible();
    const chips = page.getByTestId('ai-chat-message-work-focus');
    // The malformed focus is dropped, not shown as a link.
    await expect(chips).toHaveCount(4);

    const current = chips.nth(0);
    await expect(current).toHaveAttribute('data-work-focus-state', 'current');
    await expect(current.getByRole('link')).toHaveAttribute(
      'href',
      '/maintenance/work-orders/73'
    );
    await expect(current.getByText('Changed since this answer')).toHaveCount(0);

    const changed = chips.nth(1);
    await expect(changed.getByRole('link')).toHaveAttribute(
      'href',
      '/maintenance/work-orders/74'
    );
    const changedBadge = changed.getByText('Changed since this answer', {
      exact: true
    });
    await expect(changedBadge).toBeVisible();
    // Keyboard users reach the explanation too: Tab from the Work link.
    await changed.getByRole('link').focus();
    await page.keyboard.press('Tab');
    await expect(
      changed.getByLabel(
        /^Changed since this answer\. This work order's status/
      )
    ).toBeFocused();
    await expect(
      page.getByText(
        "This work order's status, machine or access has changed since this answer. Check the work order before relying on it."
      )
    ).toBeVisible();

    const withdrawn = chips.nth(2);
    await expect(withdrawn.getByRole('link')).toHaveCount(0);
    await expect(withdrawn.getByText('Work order 75')).toBeVisible();
    await expect(
      withdrawn.getByText('No longer available to you')
    ).toBeVisible();

    // A state this client does not know is shown as nothing, never guessed.
    const unknown = chips.nth(3);
    await expect(unknown).toHaveAttribute('data-work-focus-state', 'live');
    await expect(unknown.getByRole('link')).toHaveAttribute(
      'href',
      '/maintenance/work-orders/76'
    );

    await page.screenshot({
      path: testInfo.outputPath('work-assistant-reload-states.png'),
      animations: 'disabled'
    });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.screenshot({
      path: testInfo.outputPath('work-assistant-reload-states-dark.png'),
      animations: 'disabled'
    });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(page.viewportSize()!.width);
  });
});
