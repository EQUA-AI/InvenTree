import { expect, test } from '@playwright/test';
import {
  defaultCapability,
  installVoiceMocks,
  readMockState,
  startVoice
} from './voice_harness';

test.beforeEach(async ({ page }) => {
  await page.route('**/*', (route) =>
    ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort()
  );
});

for (const slow of ['revalidation', 'execution']) {
  test(`screen approval waits for slow ${slow} without redispatch`, async ({
    page
  }) => {
    const id = '895b150b-2d71-4d7d-a9a6-bed00edc6890';
    let status = 'in_review';
    let confirming = false;
    let delayed = false;
    const writes: string[] = [];
    await page.route('**/api/approvals/**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() === 'POST') {
        writes.push(path);
        if (path.endsWith('/approve/')) {
          expect(route.request().postDataJSON()).toMatchObject({
            revision: 0,
            review_hash: 'same-review'
          });
          if (slow === 'execution')
            await new Promise((resolve) => setTimeout(resolve, 6000));
          status = 'succeeded';
        }
        await route.fulfill({ json: { status } });
      } else if (path.endsWith('/card-package/')) {
        if (slow === 'revalidation' && confirming && !delayed) {
          delayed = true;
          await new Promise((resolve) => setTimeout(resolve, 6000));
        }
        await route.fulfill({
          json: {
            approval_id: id,
            summary: 'Recording-only workflow',
            action_type: 'workflow',
            status,
            risk_tier: 2,
            current_revision_number: 0,
            review_hash: 'same-review',
            payload: {},
            execution_result:
              status === 'succeeded' ? { recorded: true } : null,
            review_sections: [
              {
                id: 'summary',
                label: 'Request',
                text: 'Synthetic only; no external effect.',
                required: true
              }
            ]
          }
        });
      } else
        await route.fulfill({
          json: [
            {
              id,
              summary: 'Recording-only workflow',
              status,
              action_type: 'workflow',
              risk_tier: 2
            }
          ]
        });
    });
    await page.goto('/playwright/approval-flow.html');
    await page
      .getByRole('button', { name: 'Recording-only workflow', exact: true })
      .click();
    const panel = page.getByTestId('approval-screen-review');
    await panel.getByRole('checkbox').check();
    await panel
      .getByRole('button', { name: 'Confirm reviewed', exact: true })
      .click();
    await panel
      .getByRole('button', { name: 'Prepare approval', exact: true })
      .click();
    await panel
      .getByLabel('Type the required confirmation phrase')
      .fill('approve 895b150b');
    confirming = true;
    await panel
      .getByRole('button', { name: 'Confirm decision', exact: true })
      .click();
    await expect(page.getByTestId('approval-recorded-outcome')).toContainText(
      'succeeded',
      { timeout: 15000 }
    );
    expect(writes).toEqual([
      `/api/approvals/${id}/confirm-viewed/`,
      `/api/approvals/${id}/approve/`
    ]);
    if (slow === 'revalidation') expect(delayed).toBe(true);
  });
}

test('explicit voice route preserves the login return target', async ({
  page
}) => {
  const voice = await installVoiceMocks(page);
  await page.goto('/playwright/voice-mobile.html?signed_out');
  await expect(
    page.getByText('Sign in required. Return to: /voice')
  ).toBeVisible();
  expect(voice.sessionCreates).toHaveLength(0);
});

test('disabled voice is honest and the full-app escape remains available', async ({
  page
}) => {
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, enabled: false }
  });
  await page.goto('/playwright/voice-mobile.html');
  await expect(
    page.getByText('Voice is unavailable. You can continue in the full app.')
  ).toBeVisible();
  await expect(page.getByTestId('voice-start')).toHaveCount(0);
  await page
    .getByRole('link', { name: 'Open full app (not optimized for phones)' })
    .click();
  await expect(page.getByText('Full app fixture')).toBeVisible();
  expect(voice.sessionCreates).toHaveLength(0);
});

test('explicit route survives rotation, escape and logout resets the override', async ({
  page
}) => {
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  await page.goto('/playwright/voice-mobile.html');
  await startVoice(page);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  for (const viewport of [
    { width: 393, height: 851 },
    { width: 851, height: 393 },
    { width: 393, height: 320 }
  ]) {
    await page.setViewportSize(viewport);
    await expect(page.getByTestId('voice-mobile-page')).toBeVisible();
    expect((await readMockState(page)).trackStopped).toBe(false);
  }
  await page
    .getByRole('link', { name: 'Open full app (not optimized for phones)' })
    .click();
  expect(voice.sessionCreates).toHaveLength(1);
  expect((await readMockState(page)).trackStopped).toBe(false);
  await page.getByRole('link', { name: 'Return to voice fixture' }).click();
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  expect(voice.sessionCreates).toHaveLength(1);
  await page.getByRole('link', { name: 'Log out' }).click();
  await expect(
    page.getByText('Signed out. Full app preference: false')
  ).toBeVisible();
  await expect.poll(() => voice.sessionEnded).toBe(true);
  expect((await readMockState(page)).trackStopped).toBe(true);
});

test('guided steps use the active session and only request completion review', async ({
  page
}) => {
  const voice = await installVoiceMocks(page, {
    capability: {
      ...defaultCapability,
      foreground_session: true,
      guided_procedures: true,
      procedure_complete: true
    }
  });
  const requests: Record<string, unknown>[] = [];
  await page.route('**/api/ai/voice/procedures/walkthrough', async (route) => {
    const body = route.request().postDataJSON();
    requests.push(body);
    const text =
      body.utterance === 'done'
        ? 'Review step completion. No change has been submitted.'
        : 'Step 1 of 2. Verify fifty PSI.';
    await route.fulfill({
      json: {
        position: 0,
        total: 2,
        speak_text: text,
        pending_decision: null,
        spoken: {
          utterance_id: 'step-review',
          spoken_summary: text,
          spoken_summary_hash: 'mock-step-hash',
          playback_state: 'pending'
        }
      }
    });
  });
  await page.goto('/playwright/voice-mobile.html');
  await startVoice(page);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await page.getByLabel('Work order ID', { exact: true }).fill('42');
  await page.getByRole('button', { name: 'Read step', exact: true }).click();
  await expect(
    page
      .getByTestId('voice-procedure-controls')
      .getByText('Step 1 of 2. Verify fifty PSI.')
  ).toBeVisible();
  await page.getByLabel('Measured value (literal units)').fill('50');
  await page.getByLabel('Step result', { exact: true }).selectOption('true');
  await page.getByRole('button', { name: 'Review step completion' }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toMatchObject({
    work_order_id: 42,
    position: 0,
    utterance: 'done',
    value: '50',
    passed: true
  });
  expect(requests[1].session_id).toBeTruthy();
  expect(voice.decisionActions).toHaveLength(0);
  await page.getByTestId('voice-end').click();
});

test('pilot phone defaults to voice and preserves one session across escape and resize', async ({
  page
}) => {
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  await page.addInitScript(() => {
    (window as any).INVENTREE_SETTINGS = { voice_phone_short_edge_px: 600 };
    Object.defineProperty(navigator, 'maxTouchPoints', {
      value: 5,
      configurable: true
    });
    Object.defineProperty(window.screen, 'width', {
      value: 393,
      configurable: true
    });
    Object.defineProperty(window.screen, 'height', {
      value: 851,
      configurable: true
    });
    const matchMedia = window.matchMedia.bind(window);
    window.matchMedia = (query) =>
      query === '(pointer: coarse)'
        ? ({ ...matchMedia(query), matches: true } as MediaQueryList)
        : matchMedia(query);
  });
  await page.goto('/playwright/voice-mobile.html?pilot');
  await expect(page.getByTestId('voice-mobile-page')).toBeVisible();
  await startVoice(page);
  for (const viewport of [
    { width: 851, height: 393 },
    { width: 393, height: 320 }
  ]) {
    await page.setViewportSize(viewport);
    await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
    expect((await readMockState(page)).trackStopped).toBe(false);
  }
  await page
    .getByRole('link', { name: 'Open full app (not optimized for phones)' })
    .click();
  await expect(page.getByText('Full app fixture')).toBeVisible();
  await page.getByRole('link', { name: 'Back to voice', exact: true }).click();
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  expect(voice.sessionCreates).toHaveLength(1);
  await page.getByRole('link', { name: 'Log out' }).click();
  await expect(
    page.getByText('Signed out. Full app preference: false')
  ).toBeVisible();
});

test('pilot tablet remains in full app even with a short keyboard viewport', async ({
  page
}) => {
  await installVoiceMocks(page);
  await page.addInitScript(() => {
    (window as any).INVENTREE_SETTINGS = { voice_phone_short_edge_px: 600 };
    Object.defineProperty(navigator, 'maxTouchPoints', {
      value: 5,
      configurable: true
    });
    Object.defineProperty(window.screen, 'width', {
      value: 768,
      configurable: true
    });
    Object.defineProperty(window.screen, 'height', {
      value: 1024,
      configurable: true
    });
  });
  await page.goto('/playwright/voice-mobile.html?pilot');
  await page.setViewportSize({ width: 768, height: 300 });
  await expect(page.getByText('Full app fixture')).toBeVisible();
  await expect(page.getByTestId('voice-mobile-page')).toHaveCount(0);
});

test('network estimates keep listening; actual interface handoff pauses without resending', async ({
  page
}) => {
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  const suspends: string[] = [];
  page.on('request', (request) => {
    if (request.url().endsWith('/suspend')) suspends.push(request.url());
  });
  await page.addInitScript(() => {
    const connection = Object.assign(new EventTarget(), {
      type: 'wifi',
      effectiveType: '4g',
      rtt: 20,
      downlink: 10
    });
    Object.defineProperty(navigator, 'connection', {
      value: connection,
      configurable: true
    });
  });
  await page.goto('/playwright/voice-mobile.html');
  await startVoice(page);
  await page.evaluate(() => {
    const connection = (navigator as any).connection;
    Object.assign(connection, { effectiveType: '3g', rtt: 400, downlink: 0.5 });
    connection.dispatchEvent(new Event('change'));
  });
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  expect((await readMockState(page)).trackEnabled).toBe(true);
  expect(suspends).toHaveLength(0);
  await page.evaluate(() => {
    const connection = (navigator as any).connection;
    connection.type = 'cellular';
    connection.dispatchEvent(new Event('change'));
  });
  await expect
    .poll(async () => (await readMockState(page)).trackEnabled)
    .toBe(false);
  await expect.poll(() => suspends.length).toBeGreaterThan(0);
  expect(voice.turns).toHaveLength(0);
  expect(voice.sessionCreates).toHaveLength(1);
  await page.getByTestId('voice-end').click();
});
