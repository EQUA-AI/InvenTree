import type { Page } from '@playwright/test';

import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';

/**
 * WS5/VA3 browser coverage for the realtime voice states that can be proven
 * without a live Azure provider: capability gating, honest server rejection,
 * and microphone-denial recovery. Transport/TTS behavior needs the real
 * provider and stays in the target-host validation matrix.
 */

async function openChat(page: Page) {
  await page.getByLabel('open-ai-chat').click();
  await expect(
    page.getByText('AI Assistant', { exact: true }).first()
  ).toBeVisible();
}

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
  await page.route('**/api/ai/voice/capability', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ enabled: true, webrtc: true, relay: false })
    });
  });
  // The drawer mounts during login, before the mock existed; reload so the
  // capability probe fires against the mocked route.
  await page.reload();
  await openChat(page);
  await page.getByTestId('voice-start').click();
  // The real backend still has the feature disabled, so session creation
  // is rejected; the UI must show the stable code, not a live-looking mic.
  await expect(page.getByTestId('voice-error')).toHaveText(
    'VOICE_SESSION_UNAVAILABLE'
  );
  await expect(page.getByTestId('voice-state-badge')).toHaveCount(0);
});

test('microphone denial fails honestly and cleans up the session', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  let sessionEnded = false;
  await page.route('**/api/ai/voice/capability', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ enabled: true, webrtc: true, relay: false })
    });
  });
  await page.route('**/api/ai/voice/sessions', async (route) => {
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: '11111111-1111-1111-1111-111111111111',
        state: 'created',
        thread_id: 'thread_mocked',
        transport: null,
        transports_allowed: { webrtc: true, relay: false },
        webrtc_preview: true,
        turn_count: 0,
        policy_version: 'test',
        terminal_reason: null
      })
    });
  });
  await page.route('**/api/ai/voice/sessions/*', async (route) => {
    if (route.request().method() === 'DELETE') {
      sessionEnded = true;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'mocked', state: 'ended' })
    });
  });
  // The drawer mounts during login, before the mock existed; reload so the
  // capability probe fires against the mocked route.
  await page.reload();
  await openChat(page);
  await page.getByTestId('voice-start').click();
  // Headless Chromium denies getUserMedia without explicit grants, so the
  // hook must end the server session and report the denial.
  await expect(page.getByTestId('voice-error')).toHaveText('MICROPHONE_DENIED');
  await expect.poll(() => sessionEnded).toBe(true);
});

test('closing the drawer ends voice and releases the microphone', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  let sessionEnded = false;
  await page.addInitScript(() => {
    const track = {
      enabled: true,
      stop: () => {
        (window as any).__voiceTrackStopped = true;
      }
    };
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => ({
          getTracks: () => [track],
          getAudioTracks: () => [track]
        })
      }
    });
    class MockDataChannel {
      addEventListener() {}
    }
    class MockPeerConnection {
      connectionState = 'connected';
      addTrack() {}
      addEventListener() {}
      createDataChannel() {
        return new MockDataChannel();
      }
      async createOffer() {
        return { type: 'offer', sdp: 'v=0\r\nmock-offer' };
      }
      async setLocalDescription() {}
      async setRemoteDescription() {}
      close() {}
    }
    (window as any).RTCPeerConnection = MockPeerConnection;
  });
  await page.route('**/api/ai/voice/capability', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ enabled: true, webrtc: true, relay: false })
    });
  });
  await page.route('**/api/ai/voice/sessions', async (route) => {
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: '22222222-2222-2222-2222-222222222222',
        state: 'created',
        thread_id: 'thread_mocked',
        transport: null,
        transports_allowed: { webrtc: true, relay: false },
        webrtc_preview: true,
        turn_count: 0,
        policy_version: 'test',
        terminal_reason: null
      })
    });
  });
  await page.route('**/api/ai/voice/sessions/*/sdp', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ sdp_answer: 'v=0\r\nmock-answer' })
    });
  });
  await page.route('**/api/ai/voice/sessions/*', async (route) => {
    if (route.request().method() === 'DELETE') {
      sessionEnded = true;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'mocked', state: 'ended' })
    });
  });

  await page.reload();
  await openChat(page);
  await page.getByTestId('voice-start').click();
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await page.getByLabel('close-ai-chat').click();

  await expect.poll(() => sessionEnded).toBe(true);
  await expect
    .poll(() => page.evaluate(() => (window as any).__voiceTrackStopped))
    .toBe(true);
});
