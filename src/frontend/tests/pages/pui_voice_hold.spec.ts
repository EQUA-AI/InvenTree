import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
import {
  emitTranscript,
  installVoiceMocks,
  openChat
} from './voice_harness.js';

/**
 * Transcript-review hold behaviour driven through the real data channel
 * (voice-UX plan A9): bare decisions are forwarded, critical values are
 * held and read back by the server, corrections revise the hold, and a
 * bare cancel discards it.
 */

async function startListening(page: Awaited<ReturnType<typeof doCachedLogin>>) {
  await page.reload();
  await openChat(page);
  await page.getByTestId('voice-start').click();
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
}

test('bare "no" with no hold is forwarded, not held', async ({ browser }) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  const voice = await installVoiceMocks(page);
  await startListening(page);

  await emitTranscript(page, { text: 'No.', itemId: 'item-no' });

  await expect.poll(() => voice.turns.length).toBe(1);
  expect(voice.turns[0].body?.transcript).toBe('No.');
  await expect(page.getByTestId('voice-pending-transcript')).toHaveCount(0);
  expect(voice.prompts).toHaveLength(0);
});

test('a critical value is held and the server is asked to read it back', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  const voice = await installVoiceMocks(page);
  await startListening(page);

  await emitTranscript(page, {
    text: 'reading is 50 psi',
    itemId: 'item-psi'
  });

  await expect(page.getByTestId('voice-pending-transcript')).toContainText(
    'reading is 50 psi'
  );
  await expect.poll(() => voice.prompts.length).toBe(1);
  expect(voice.prompts[0].body).toMatchObject({
    kind: 'transcript_review',
    transcript: 'reading is 50 psi'
  });
  // The client never sends speech text for TTS: only the transcript slot.
  expect(voice.prompts[0].body).not.toHaveProperty('spoken_summary');
  await expect(page.getByTestId('voice-hold-prompt-state')).toContainText(
    'Read back aloud'
  );
  expect(voice.turns).toHaveLength(0);
});

test('speech during a hold becomes a new revision and re-prompts', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  const voice = await installVoiceMocks(page);
  await startListening(page);

  await emitTranscript(page, {
    text: 'reading is 50 psi',
    itemId: 'item-1'
  });
  await expect(page.getByTestId('voice-pending-transcript')).toContainText(
    '50 psi'
  );
  await emitTranscript(page, { text: '15 psi', itemId: 'item-2' });

  await expect(page.getByTestId('voice-pending-transcript')).toContainText(
    '15 psi'
  );
  await expect(page.getByTestId('voice-pending-transcript')).toContainText(
    'revision 2'
  );
  await expect.poll(() => voice.prompts.length).toBe(2);
  expect(voice.prompts[1].body).toMatchObject({ transcript: '15 psi' });
  expect(voice.turns).toHaveLength(0);

  await emitTranscript(page, { text: 'Confirm', itemId: 'item-3' });
  await expect.poll(() => voice.turns.length).toBe(1);
  expect(voice.turns[0].body?.transcript).toBe('15 psi');
  await expect(page.getByTestId('voice-pending-transcript')).toHaveCount(0);
});

test('a bare cancel during a hold discards it without a turn', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  const voice = await installVoiceMocks(page);
  await startListening(page);

  await emitTranscript(page, {
    text: 'reading is 50 psi',
    itemId: 'item-1'
  });
  await expect(page.getByTestId('voice-pending-transcript')).toBeVisible();
  await emitTranscript(page, { text: 'cancel', itemId: 'item-2' });

  await expect(page.getByTestId('voice-pending-transcript')).toHaveCount(0);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  expect(voice.turns).toHaveLength(0);
});

test('a failed prompt request is shown honestly instead of pretending it played', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  const voice = await installVoiceMocks(page, {
    onPrompt: () => {
      throw new Error('route should be overridden');
    }
  });
  await page.route('**/api/ai/voice/sessions/*/prompts', async (route) => {
    await route.fulfill({
      status: 503,
      json: { detail: 'VOICE_TRANSPORT_UNAVAILABLE' }
    });
  });
  await startListening(page);

  await emitTranscript(page, {
    text: 'reading is 50 psi',
    itemId: 'item-1'
  });

  await expect(page.getByTestId('voice-pending-transcript')).toBeVisible();
  await expect(page.getByTestId('voice-hold-prompt-state')).toContainText(
    'could not be played'
  );
  expect(voice.turns).toHaveLength(0);
});
