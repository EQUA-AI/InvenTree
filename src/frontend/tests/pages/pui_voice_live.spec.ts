import { expect, test } from '../baseFixtures.js';
import { readeruser } from '../defaults.js';
import { doCachedLogin } from '../login.js';
import { installVoiceMocks, openChat, readMockState } from './voice_harness.js';

/**
 * WS5/VA3 browser coverage for the realtime voice states that can be proven
 * without a live Azure provider: capability gating, honest server rejection,
 * microphone-denial recovery and session cleanup. Ported onto the shared
 * voice harness (P0-13) with the original assertions unchanged; transport
 * and TTS behavior still need the real provider and stay in the target-host
 * validation matrix.
 */

test('reader sees voice control when Voice is enabled', async ({ browser }) => {
  const page = await doCachedLogin(browser, { user: readeruser, url: 'home' });
  // The capability is mocked as enabled so this proves the control renders
  // for a reader when the SERVER says voice is on, independently of the
  // deployment flag (the next test covers the flag-off deployment).
  await installVoiceMocks(page, { mockSessions: false });
  await page.reload();

  await openChat(page);

  await expect(page.getByTestId('voice-session-control')).toBeVisible();
  await expect(page.getByTestId('voice-start')).toBeVisible();
});

test('voice control is absent while the server capability is off', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await openChat(page);
  // The deployment in this run keeps FEATURE_VOICE_LIVE=false, so the
  // capability probe returns disabled and no voice control may render.
  await expect(page.getByTestId('voice-session-control')).toHaveCount(0);
  await expect(page.getByTestId('voice-start')).toHaveCount(0);
});

test('server rejection surfaces an honest error instead of a fake session', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  // Only the capability is mocked; the real backend still has the feature
  // disabled, so session creation is rejected.
  await installVoiceMocks(page, { mockSessions: false });
  // The drawer mounts during login, before the mock existed; reload so the
  // capability probe fires against the mocked route.
  await page.reload();
  await openChat(page);
  await page.getByTestId('voice-start').click();
  // The UI must show the stable code, not a live-looking mic.
  await expect(page.getByTestId('voice-error')).toHaveText(
    'VOICE_SESSION_UNAVAILABLE'
  );
  await expect(page.getByTestId('voice-state-badge')).toHaveCount(0);
});

test('microphone denial fails honestly and cleans up the session', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  const voice = await installVoiceMocks(page, { denyMicrophone: true });
  await page.reload();
  await openChat(page);
  await page.getByTestId('voice-start').click();
  // The hook must end the server session and report the denial.
  await expect(page.getByTestId('voice-error')).toHaveText('MICROPHONE_DENIED');
  await expect.poll(() => voice.sessionEnded).toBe(true);
});

test('closing the drawer ends voice and releases the microphone', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  const voice = await installVoiceMocks(page);
  await page.reload();
  await openChat(page);
  await page.getByTestId('voice-start').click();
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await page.getByLabel('close-ai-chat').click();

  await expect.poll(() => voice.sessionEnded).toBe(true);
  await expect
    .poll(async () => (await readMockState(page)).trackStopped)
    .toBe(true);
});
