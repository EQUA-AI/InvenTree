import { expect, test } from '@playwright/test';
import {
  defaultCapability,
  emitTranscript,
  installVoiceMocks,
  readMockState,
  startVoice,
  turnPayload
} from './voice_harness';

test.beforeEach(async ({ page }) => {
  // This suite must never reach the container or any provider.
  await page.route('**/*', (route) =>
    ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort()
  );
});

test('synthetic interim/final RTP fixture reports once without decision authority or business replay', async ({
  page
}) => {
  const reports: Record<string, any>[] = [];
  const spoken = {
    utterance_id: '11111111-1111-1111-1111-111111111111',
    spoken_summary: 'Synthetic final response',
    spoken_summary_hash: 'a'.repeat(64),
    playback_state: 'requested'
  };
  const voice = await installVoiceMocks(page, {
    capability: {
      ...defaultCapability,
      foreground_session: true,
      validation_metrics: true
    },
    onTurn: () => turnPayload({ spoken })
  });
  await page.route('**/api/ai/voice/sessions/*/timing-epoch', (route) =>
    route.fulfill({ json: { epoch: 'a'.repeat(32) } })
  );
  await page.route('**/api/ai/voice/sessions/*/timing', (route) => {
    reports.push(route.request().postDataJSON());
    return route.fulfill({
      status: 503,
      json: { detail: 'VOICE_TIMING_UNAVAILABLE' }
    });
  });
  await page.goto('/playwright/voice-session.html');
  // This fixture has no audio source. A local-only fake playing element allows
  // the real controller's stats loop to exercise synthetic energy, never live evidence.
  await page.evaluate(() =>
    Object.defineProperty(HTMLMediaElement.prototype, 'paused', {
      configurable: true,
      get: () => false
    })
  );
  await startVoice(page);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await page.evaluate(() => {
    for (const peer of (window as any).__voiceMock.peers)
      peer.dispatch('track', { streams: [new MediaStream()] });
  });
  await emitTranscript(page, {
    text: 'list machines',
    itemId: 'synthetic-timing',
    confidence: 1
  });
  await expect.poll(() => voice.turns.length).toBe(1);
  const sample = async (energy: number) => {
    const before = await page.evaluate((value) => {
      const m = (window as any).__voiceMock;
      m.timingEnergy = value;
      return m.statsPolls;
    }, energy);
    await expect
      .poll(() => page.evaluate(() => (window as any).__voiceMock.statsPolls))
      .toBeGreaterThan(before);
  };
  await sample(0);
  await page.evaluate(() => {
    const m = (window as any).__voiceMock;
    m.emit('response.created', { response: { id: 'synthetic-interim' } });
    m.emit('response.audio_transcript.delta', {
      response_id: 'synthetic-interim',
      delta: 'One moment.'
    });
    m.emit('output_audio_buffer.started');
  });
  await sample(1);
  await page.evaluate(() => {
    const m = (window as any).__voiceMock;
    m.emit('response.audio_transcript.done', {
      response_id: 'synthetic-interim',
      transcript: 'One moment.'
    });
    m.emit('response.audio.done', { response_id: 'synthetic-interim' });
    m.emit('output_audio_buffer.stopped');
    m.emit('response.done', {
      response: { id: 'synthetic-interim', status: 'completed' }
    });
  });
  await sample(1); // Quiet after drain, before the next output is created.
  await page.evaluate(() => {
    const m = (window as any).__voiceMock;
    m.emit('response.created', { response: { id: 'synthetic-final' } });
    m.emit('response.audio_transcript.delta', {
      response_id: 'synthetic-final',
      delta: 'Synthetic'
    });
    m.emit('output_audio_buffer.started');
  });
  await sample(2);
  await page.evaluate((text) => {
    const m = (window as any).__voiceMock;
    m.emit('response.audio_transcript.done', {
      response_id: 'synthetic-final',
      transcript: text
    });
    m.emit('response.audio.done', { response_id: 'synthetic-final' });
    m.emit('output_audio_buffer.stopped');
  }, spoken.spoken_summary);
  await expect.poll(() => reports.length).toBe(1);
  expect(reports[0].provenance).toBe('rtp_energy_proxy');
  expect(reports[0].timing.submit_to_observed_playback_ms).toBeGreaterThan(0);
  expect(JSON.stringify(reports)).not.toContain(spoken.spoken_summary);
  expect(voice.decisionActions).toHaveLength(0);
  expect(voice.turns).toHaveLength(1);
  await page.getByTestId('voice-end').click();
});

test('unavailable startup is visible, cannot request microphone/session, and recovers', async ({
  page
}) => {
  const voice = await installVoiceMocks(page, {
    capability: {
      ...defaultCapability,
      foreground_session: true,
      runtime: {
        state: 'transiently_unavailable',
        available: false,
        reason: 'provider_throttled',
        retry_after_s: 1
      }
    }
  });
  await page.goto('/playwright/voice-session.html');
  await expect(page.getByTestId('voice-runtime-unavailable')).toContainText(
    'temporarily unavailable'
  );
  await expect(page.getByTestId('voice-start')).toBeDisabled();
  await page.keyboard.press('Control+Shift+V');
  await expect(
    page.getByRole('dialog', { name: 'Start a voice session' })
  ).toHaveCount(0);
  expect(voice.sessionCreates).toHaveLength(0);
  await page.route('**/api/ai/voice/capability', (route) =>
    route.fulfill({
      json: {
        ...defaultCapability,
        foreground_session: true,
        runtime: {
          state: 'ready',
          available: true,
          reason: null,
          retry_after_s: null
        }
      }
    })
  );
  await expect(page.getByTestId('voice-start')).toBeEnabled({ timeout: 10000 });
  await startVoice(page);
  expect(voice.sessionCreates).toHaveLength(1);
  await page.getByTestId('voice-end').click();
});

test('optional numeric timing cannot acknowledge delivery or retry a business turn', async ({
  page
}) => {
  const reports: Record<string, unknown>[] = [];
  let epochs = 0;
  const voice = await installVoiceMocks(page, {
    capability: {
      ...defaultCapability,
      foreground_session: true,
      validation_metrics: true
    },
    onTurn: () =>
      turnPayload({
        spoken: {
          utterance_id: '11111111-1111-1111-1111-111111111111',
          spoken_summary: 'PRIVATE_SENTINEL',
          spoken_summary_hash: 'a'.repeat(64),
          playback_state: 'requested'
        }
      })
  });
  await page.route('**/api/ai/voice/sessions/*/timing-epoch', async (route) => {
    epochs++;
    await route.fulfill({ json: { epoch: 'a'.repeat(32) } });
  });
  await page.route('**/api/ai/voice/sessions/*/timing', async (route) => {
    reports.push(route.request().postDataJSON());
    await route.fulfill({
      status: 503,
      json: { detail: 'VOICE_TIMING_UNAVAILABLE' }
    });
  });
  await page.goto('/playwright/voice-session.html');
  await startVoice(page);
  await expect.poll(() => epochs).toBe(1);
  await page.evaluate(() =>
    (window as any).__voiceMock.emit('input_audio_buffer.speech_stopped', {
      item_id: 'timing-item'
    })
  );
  await emitTranscript(page, {
    text: 'hello there',
    itemId: 'timing-item',
    confidence: 1
  });
  await expect.poll(() => voice.turns.length).toBe(1);
  const body = voice.turns[0].body as { timing: Record<string, number> };
  expect(Object.keys(body.timing).sort()).toEqual([
    'final_to_submit_ms',
    'speech_to_final_ms'
  ]);
  expect(
    Object.values(body.timing).every(
      (value) => typeof value === 'number' && value >= 0
    )
  ).toBe(true);
  await page.getByTestId('voice-stop-speaking').click();
  await expect.poll(() => reports.length).toBe(1);
  expect(reports[0].provenance).toBe('local_pause_proxy');
  expect(reports[0]).not.toHaveProperty('first_playback_epoch_ms');
  expect(JSON.stringify(reports)).not.toContain('PRIVATE_SENTINEL');
  expect(voice.turns).toHaveLength(1);
  expect(voice.decisionActions).toHaveLength(0);
  await page.getByTestId('voice-end').click();
  expect(voice.turns).toHaveLength(1);
});

test('sounds command is local and PTT preference survives a new session', async ({
  page
}) => {
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  await page.goto('/playwright/voice-session.html');
  await expect(page.getByTestId('voice-start')).toBeVisible();
  await page.keyboard.press('Control+Shift+V');
  await expect(
    page.getByRole('dialog', { name: 'Start a voice session' })
  ).toBeVisible();
  await page
    .getByLabel('I have read the disclosure and want to start voice.')
    .check();
  await page.getByTestId('voice-consent-start').click();
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await expect(
    page.getByLabel('Voice status sounds', { exact: true })
  ).toBeChecked();
  await emitTranscript(page, {
    text: 'turn sounds off',
    itemId: 'sounds-off',
    confidence: 1
  });
  await expect(
    page.getByLabel('Voice status sounds', { exact: true })
  ).not.toBeChecked();
  expect(voice.turns).toHaveLength(0);
  await page.getByLabel('Listening mode').selectOption('push_to_talk');
  await page.getByTestId('voice-end').click();
  await startVoice(page);
  await expect(page.getByLabel('Listening mode')).toHaveValue('push_to_talk');
  expect((await readMockState(page)).trackEnabled).toBe(false);
  await page.getByTestId('voice-end').click();
});

test('progressive output controls do not submit another application turn', async ({
  page
}) => {
  const spoken = {
    utterance_id: 'u1',
    spoken_summary:
      'Warning: isolate power. Four items. Alpha, Bravo, Charlie.',
    spoken_summary_hash: 'h',
    playback_state: 'requested'
  };
  const presentation = { id: 'p1', source_hash: 'h', index: 0, total: 2 };
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true },
    onTurn: () => turnPayload({ spoken, presentation })
  });
  const commands: string[] = [];
  await page.route('**/api/ai/voice/sessions/*/presentation', async (route) => {
    const body = route.request().postDataJSON();
    commands.push(body.presentation_command);
    await route.fulfill({
      json: {
        presentation: { ...presentation, index: 1 },
        spoken: { ...spoken, spoken_summary: 'Warning: isolate power. Delta.' }
      }
    });
  });
  await page.goto('/playwright/voice-session.html');
  await startVoice(page);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await emitTranscript(page, {
    text: 'list the available machines',
    itemId: 'list-one',
    confidence: 1
  });
  await expect(page.getByText('Page 1 of 2')).toBeVisible();
  await page.getByRole('button', { name: 'Next three', exact: true }).click();
  await expect(page.getByText('Page 2 of 2')).toBeVisible();
  await page
    .getByRole('button', { name: 'Short version', exact: true })
    .click();
  await expect.poll(() => commands).toEqual(['next', 'short']);
  expect(voice.turns).toHaveLength(1);
  expect(voice.decisionActions).toHaveLength(0);
  await page.getByTestId('voice-end').click();
});

test('consent is required, defaults are mapped, and sample never starts a session', async ({
  page
}) => {
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  await page.route('**/api/ai/voice/sample', (route) =>
    route.fulfill({ status: 503, json: { detail: 'VOICE_SAMPLE_UNAVAILABLE' } })
  );
  await page.goto('/playwright/voice-session.html');
  await page.getByTestId('voice-start').click();
  await expect(page.getByLabel('Language and output voice')).toHaveValue(
    'en-US'
  );
  await page.getByTestId('voice-consent-start').click();
  await expect(
    page.getByText('Please review and accept the disclosure.')
  ).toBeVisible();
  expect(voice.sessionCreates).toHaveLength(0);
  await page.getByRole('button', { name: 'Hear a voice sample' }).click();
  await expect(
    page.getByText(
      'The sample could not play. Please wait a minute and try again.'
    )
  ).toBeVisible();
  expect(voice.sessionCreates).toHaveLength(0);
  await page
    .getByLabel('I have read the disclosure and want to start voice.')
    .check();
  await page.getByTestId('voice-consent-start').click();
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  expect(voice.sessionCreates).toHaveLength(1);
  expect(voice.sessionCreates[0].body).toMatchObject({
    consent_version: 'consent-v3-memory',
    locale: 'en-US',
    voice: 'en-US-AvaNeural'
  });
  await page.getByTestId('voice-end').click();
});

test('StrictMode session survives panel close and route navigation, global view reuses it', async ({
  page
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  await page.goto('/playwright/voice-session.html');
  await startVoice(page);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await page.getByRole('button', { name: 'Navigate and toggle panel' }).click();
  await expect(page.getByTestId('voice-state-badge')).toHaveCount(0);
  expect(voice.sessionEnded).toBe(false);
  expect((await readMockState(page)).trackStopped).toBe(false);
  await page.getByLabel('Open hands-free voice').click();
  await expect(page.getByTestId('voice-hands-free')).toBeVisible();
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  expect(voice.sessionCreates).toHaveLength(1);
  await expect
    .poll(() =>
      page
        .getByTestId('voice-session-control')
        .getByRole('button')
        .evaluateAll((nodes) =>
          nodes.every((node) => node.getBoundingClientRect().height >= 44)
        )
    )
    .toBe(true);
  await page.getByTestId('voice-end').click();
  expect(errors).toEqual([]);
});

test('PTT keyboard is focus scoped and releases on blur; shortcut ignores barcode input', async ({
  page
}) => {
  await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  await page.goto('/playwright/voice-session.html');
  await startVoice(page);
  await page.getByLabel('Listening mode').selectOption('push_to_talk');
  expect((await readMockState(page)).trackEnabled).toBe(false);
  await page.getByTestId('voice-ptt').focus();
  await page.keyboard.down('Space');
  expect((await readMockState(page)).trackEnabled).toBe(true);
  await page.getByLabel('Barcode input').focus();
  expect((await readMockState(page)).trackEnabled).toBe(false);
  await page.keyboard.up('Space');
  await page.keyboard.press('Control+Shift+V');
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await page.getByTestId('voice-end').click();
});

test('hidden tab suspends, return refreshes without assent, logout releases media', async ({
  page
}) => {
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  await page.goto('/playwright/voice-session.html');
  await startVoice(page);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      value: true
    });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  expect((await readMockState(page)).trackEnabled).toBe(false);
  await expect(
    page
      .locator('[data-voice-surface]')
      .getByText(
        'Voice is paused while this tab is hidden. Pending confirmation is set aside.'
      )
  ).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      value: false
    });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(() => voice.decisionReads.length).toBeGreaterThan(1);
  expect(voice.decisionActions).toHaveLength(0);
  await page.getByRole('button', { name: 'Test logout' }).click();
  await expect.poll(() => voice.sessionEnded).toBe(true);
  expect((await readMockState(page)).trackStopped).toBe(true);
});

test('hands-free layout survives portrait, landscape and keyboard-sized viewports', async ({
  page
}) => {
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  await page.goto('/playwright/voice-session.html');
  await startVoice(page);
  await page.getByRole('button', { name: 'Navigate and toggle panel' }).click();
  await page.getByLabel('Open hands-free voice').click();
  const surface = page.getByTestId('voice-hands-free');
  for (const viewport of [
    { width: 393, height: 851 },
    { width: 851, height: 393 },
    { width: 393, height: 320 }
  ]) {
    await page.setViewportSize(viewport);
    await expect(surface).toBeVisible();
    expect(
      await surface.evaluate(
        (element) => element.scrollWidth <= element.clientWidth + 1
      )
    ).toBe(true);
    expect(voice.sessionCreates).toHaveLength(1);
    expect((await readMockState(page)).trackStopped).toBe(false);
    await expect
      .poll(() =>
        surface
          .getByRole('button')
          .evaluateAll((buttons) =>
            buttons.every(
              (button) => button.getBoundingClientRect().height >= 44
            )
          )
      )
      .toBe(true);
  }
  await page.getByTestId('voice-end').click();
});

test('network interruption never resubmits and requires explicit microphone rearm', async ({
  page
}) => {
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  await page.goto('/playwright/voice-session.html');
  await startVoice(page);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await emitTranscript(page, {
    text: 'read the last work order',
    itemId: 'network-test',
    confidence: 1
  });
  await expect.poll(() => voice.turns.length).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new Event('offline')));
  expect((await readMockState(page)).trackEnabled).toBe(false);
  await expect(
    page
      .locator('[data-voice-surface]')
      .getByText(
        'Voice reconnected. No request was resubmitted. Review the last action status, then unmute to resume listening.'
      )
  ).toBeVisible();
  expect(voice.turns).toHaveLength(1);
  expect((await readMockState(page)).trackEnabled).toBe(false);
  await page.getByTestId('voice-mute').click();
  expect((await readMockState(page)).trackEnabled).toBe(true);
  await page.getByTestId('voice-end').click();
});
