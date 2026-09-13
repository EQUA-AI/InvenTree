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
    consent_version: 'consent-v2',
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
