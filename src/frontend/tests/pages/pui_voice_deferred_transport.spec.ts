/**
 * VOICE-H03 + VOICE-H08 deterministic transport acceptance — mocked network
 * and synthetic media, REAL mounted controller + live hook
 * (playwright/voice-deferred-transport.tsx).
 *
 * VOICE-H03: every count/payload assertion runs against REAL observed
 * Playwright requests via `voice_harness.ts`; `whenObserved(kind, count)`
 * resolves only when N real requests are fully recorded (the /sdp request's
 * metadata is deliberately deferred with `slowObservation` while the UI is
 * visibly `connecting`). The seam never resolves early and a request kind that
 * is never issued never resolves at all (deterministic timeout race, not a
 * flaky count). Consumer sequencing: sessionCreates → sdpOffers → turns, each
 * awaited before its exact payload is read.
 *
 * VOICE-H08: a warm-up final captured while connecting is ACTUALLY VISIBLE
 * (review strip + warmup_held notice), pre-connect bare confirm/discard speech
 * and disconnected help/sounds-off commands issue zero turns/prompts/decision
 * actions (refusal before connected), connection never automatically replays
 * the hold, explicit confirmation sends it exactly once (repeated confirm /
 * repeated spoken confirm never resend), and fresh connected capture submits
 * normally. No real microphone, no live backend, no provider calls.
 */
import { expect, test } from '@playwright/test';

import {
  emitTranscript,
  installVoiceMocks,
  mockSessionId,
  mockThreadId,
  startVoice
} from './voice_harness.js';

const warmupText = 'the old filter is still installed';
const freshText = 'please continue with the inspection summary';

function deferred(): { promise: Promise<void>; release: () => void } {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Whole-turn request payload the controller contract sends for a final. */
const turnBody = (text: string, itemId: string) => ({
  transcript: text,
  item_id: itemId,
  confidence: 0.99,
  language: 'en-US',
  decision_context: null,
  progress_mode: 'brief'
});

test('H03: deferred request metadata never fabricates observations — session → sdp → turn land exactly once in order', async ({
  page
}) => {
  const gate = deferred();
  const observations = await installVoiceMocks(page, {
    slowObservation: (kind) => (kind === 'sdpOffers' ? gate.promise : undefined)
  });
  await page.goto('/playwright/voice-deferred-transport.html');
  await expect(page.getByTestId('voice-start')).toBeVisible();
  await startVoice(page);

  // Consumer sequencing over REAL observations: each step is awaited before
  // its exact payload/count is read, and later buckets are still empty.
  await observations.whenObserved('sessionCreates', 1);
  expect(observations.sessionCreates.length).toBe(1);
  expect(observations.sdpOffers.length).toBe(0);
  expect(observations.turns.length).toBe(0);
  expect(observations.sessionCreates[0].url).toContain(
    '/api/ai/voice/sessions'
  );
  expect(observations.sessionCreates[0].body).toEqual({
    thread_id: mockThreadId,
    locale: 'en-US',
    voice: 'en-US-AvaNeural',
    consent_version: 'consent-v3-memory'
  });

  // The /sdp request is in flight with its metadata deliberately deferred:
  // the seam must NOT resolve early and nothing may be fabricated meanwhile.
  const sdpEarly = { fired: false };
  const sdpSeam = observations.whenObserved('sdpOffers', 1).then(() => {
    sdpEarly.fired = true;
  });
  // Deterministic negative: a never-issued request kind never resolves.
  const promptsEver = { fired: false };
  const promptsSeam = observations.whenObserved('prompts', 1).then(() => {
    promptsEver.fired = true;
  });
  await Promise.race([sdpSeam, sleep(500)]);
  expect(sdpEarly.fired).toBe(false);
  expect(observations.sdpOffers.length).toBe(0);
  expect(observations.turns.length).toBe(0);
  expect(observations.prompts.length).toBe(0);
  await expect(page.getByTestId('voice-state-badge')).toHaveText(/Connecting/);

  gate.release();
  await sdpSeam;
  await observations.ready();
  expect(observations.sdpOffers.length).toBe(1);
  expect(observations.sdpOffers[0].url).toContain(
    `/sessions/${mockSessionId}/sdp`
  );
  expect(observations.sdpOffers[0].body).toEqual({
    sdp_offer: 'v=0\r\nmock-offer'
  });
  expect(observations.sessionCreates.length).toBe(1);
  expect(observations.turns.length).toBe(0);

  await expect(page.getByTestId('voice-state-badge')).toHaveText(/Listening/);
  await emitTranscript(page, {
    text: freshText,
    itemId: 'turn-1',
    confidence: 0.99
  });
  await observations.whenObserved('turns', 1);
  await observations.ready();
  expect(observations.turns.length).toBe(1);
  expect(observations.turns[0].url).toContain(
    `/sessions/${mockSessionId}/turns`
  );
  expect(observations.turns[0].body).toEqual(turnBody(freshText, 'turn-1'));

  // Exact request counts in issuance order: 1 session create → 1 SDP offer →
  // 1 turn, and zero prompts/tools/writes.
  expect(observations.sessionCreates.length).toBe(1);
  expect(observations.sdpOffers.length).toBe(1);
  expect(observations.prompts.length).toBe(0);
  expect(observations.decisionActions.length).toBe(0);
  expect(observations.cancels.length).toBe(0);
  expect(observations.deletes.length).toBe(0);

  await Promise.race([promptsSeam, sleep(300)]);
  expect(promptsEver.fired).toBe(false);
  expect(observations.prompts.length).toBe(0);
});

test('H08: warm-up final visibly held and refused before connection — no auto-replay, explicit confirm sends exactly once, fresh connected capture submits', async ({
  page
}) => {
  const gate = deferred();
  const observations = await installVoiceMocks(page, {
    slowObservation: (kind) => (kind === 'sdpOffers' ? gate.promise : undefined)
  });
  await page.goto('/playwright/voice-deferred-transport.html');
  await expect(page.getByTestId('voice-start')).toBeVisible();
  await startVoice(page);
  await observations.whenObserved('sessionCreates', 1);
  await expect(page.getByTestId('voice-state-badge')).toHaveText(/Connecting/);
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).__voiceMock.channels.length)
    )
    .toBeGreaterThan(0);

  // Warm-up final captured over the live channel while still connecting.
  await emitTranscript(page, {
    text: warmupText,
    itemId: 'warm-1',
    confidence: 0.99
  });

  // H08: the hold is ACTUALLY VISIBLE — review strip + warmup_held notice.
  await expect(page.getByTestId('voice-pending-transcript')).toContainText(
    warmupText
  );
  await expect(
    page.getByText(/I heard you before the connection was ready/)
  ).toBeVisible();

  // H08: refusal before connection — bare confirm speech, disconnected
  // help/sounds-off commands and an explicit confirm request all issue ZERO
  // turns, prompts, decision reads/actions or cancels, and the hold stays.
  // Deterministic negative seams: none of these request kinds may ever be
  // issued while disconnected (a never-issued request never resolves).
  const refused = {
    turns: false,
    prompts: false,
    actions: false,
    reads: false
  };
  const refusalSeams = [
    observations.whenObserved('turns', 1).then(() => {
      refused.turns = true;
    }),
    observations.whenObserved('prompts', 1).then(() => {
      refused.prompts = true;
    }),
    observations.whenObserved('decisionActions', 1).then(() => {
      refused.actions = true;
    }),
    observations.whenObserved('decisionReads', 1).then(() => {
      refused.reads = true;
    })
  ];
  await emitTranscript(page, { text: 'yes', itemId: 'ref-yes' });
  await emitTranscript(page, { text: 'help', itemId: 'ref-help' });
  await emitTranscript(page, { text: 'turn sounds off', itemId: 'ref-sounds' });
  await page.evaluate(() => (window as any).__confirmPending());
  await Promise.race([Promise.all(refusalSeams), sleep(500)]);
  expect(refused).toEqual({
    turns: false,
    prompts: false,
    actions: false,
    reads: false
  });
  expect(observations.sessionCreates.length).toBe(1);
  expect(observations.sdpOffers.length).toBe(0);
  expect(observations.turns.length).toBe(0);
  expect(observations.prompts.length).toBe(0);
  expect(observations.decisionReads.length).toBe(0);
  expect(observations.decisionActions.length).toBe(0);
  expect(observations.cancels.length).toBe(0);
  expect(observations.deletes.length).toBe(0);
  await expect(page.getByTestId('voice-pending-transcript')).toContainText(
    warmupText
  );
  await expect(
    page.getByText(/I heard you before the connection was ready/)
  ).toBeVisible();

  // Connect completes: the review stays visible, the hold is NEVER replayed.
  gate.release();
  await observations.whenObserved('sdpOffers', 1);
  await expect(page.getByTestId('voice-state-badge')).toHaveText(
    /Confirm transcript/
  );
  await observations.ready();
  await sleep(300);
  expect(observations.turns.length).toBe(0);
  expect(observations.prompts.length).toBe(0);
  expect(observations.decisionActions.length).toBe(0);
  await expect(page.getByTestId('voice-pending-transcript')).toBeVisible();
  // The warm-up notice clears on connect; the review itself remains.
  await expect(
    page.getByText(/I heard you before the connection was ready/)
  ).toHaveCount(0);

  // H08: explicit confirmation submits the held warm-up EXACTLY ONCE.
  await page.getByTestId('voice-confirm-transcript').click();
  await observations.whenObserved('turns', 1);
  await observations.ready();
  expect(observations.turns.length).toBe(1);
  expect(observations.turns[0].url).toContain(
    `/sessions/${mockSessionId}/turns`
  );
  expect(observations.turns[0].body).toEqual(turnBody(warmupText, 'warm-1'));
  await expect(page.getByTestId('voice-pending-transcript')).toHaveCount(0);

  // Repeated explicit confirm and repeated spoken confirm never resend it.
  await page.evaluate(() => (window as any).__confirmPending());
  await emitTranscript(page, { text: 'yes', itemId: 'warm-1' });
  await observations.ready();
  await sleep(300);
  expect(observations.turns.length).toBe(1);

  // Fresh connected capture submits normally (fresh connected control).
  await emitTranscript(page, {
    text: freshText,
    itemId: 'fresh-1',
    confidence: 0.99
  });
  await observations.whenObserved('turns', 2);
  await observations.ready();
  expect(observations.turns.length).toBe(2);
  expect(observations.turns[1].body).toEqual(turnBody(freshText, 'fresh-1'));

  // Still zero prompts/tools/writes and exactly one session create + one SDP.
  expect(observations.prompts.length).toBe(0);
  expect(observations.decisionActions.length).toBe(0);
  expect(observations.sessionCreates.length).toBe(1);
  expect(observations.sdpOffers.length).toBe(1);
});
