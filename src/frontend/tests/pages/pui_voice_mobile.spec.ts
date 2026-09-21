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

test('assistant preserves the normal login return target', async ({ page }) => {
  const voice = await installVoiceMocks(page);
  await page.goto('/playwright/voice-mobile.html?signed_out');
  await expect(
    page.getByText('Sign in required. Return to: /', { exact: true })
  ).toBeVisible();
  expect(voice.sessionCreates).toHaveLength(0);
});

test('removed voice route is not found and never starts capture', async ({
  page
}) => {
  const voice = await installVoiceMocks(page);
  await page.goto('/playwright/voice-mobile.html?old_route');
  await expect(page.getByText('Page not found')).toBeVisible();
  expect(voice.sessionCreates).toHaveLength(0);
});

test('disabled voice keeps typing and close available', async ({ page }) => {
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, enabled: false }
  });
  await page.goto('/playwright/voice-mobile.html');
  await expect(
    page.getByText('Voice is unavailable. You can continue typing.')
  ).toBeVisible();
  await page.getByLabel('Message draft').fill('Keep this draft');
  await page
    .getByRole('button', { name: 'Close assistant', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Open AI Assistant', exact: true })
    .click();
  await expect(page.getByLabel('Message draft')).toHaveValue('Keep this draft');
  expect(voice.sessionCreates).toHaveLength(0);
});

test('rotation preserves capture; close stops it and reopen preserves the draft', async ({
  page
}) => {
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  await page.goto('/playwright/voice-mobile.html');
  await startVoice(page);
  await page.getByLabel('Message draft').fill('Unsaved draft');
  for (const viewport of [
    { width: 393, height: 851 },
    { width: 851, height: 393 },
    { width: 393, height: 320 }
  ]) {
    await page.setViewportSize(viewport);
    await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
    expect((await readMockState(page)).trackStopped).toBe(false);
  }
  await page
    .getByRole('button', { name: 'Close assistant', exact: true })
    .click();
  await expect.poll(() => voice.sessionEnded).toBe(true);
  expect((await readMockState(page)).trackStopped).toBe(true);
  await page
    .getByRole('button', { name: 'Open AI Assistant', exact: true })
    .click();
  await expect(page.getByLabel('Message draft')).toHaveValue('Unsaved draft');
  expect(voice.sessionCreates).toHaveLength(1);
  await expect(page.getByTestId('voice-start')).toBeVisible();
});

test('closing during microphone permission stops a late grant without hidden capture', async ({
  page
}) => {
  const voice = await installVoiceMocks(page);
  await page.addInitScript(() => {
    const acquire = navigator.mediaDevices.getUserMedia.bind(
      navigator.mediaDevices
    );
    (window as any).__permissionRequests = 0;
    navigator.mediaDevices.getUserMedia = (constraints) => {
      (window as any).__permissionRequests++;
      return new Promise((resolve, reject) => {
        (window as any).__grantPermission = () =>
          acquire(constraints).then(resolve, reject);
      });
    };
  });
  await page.goto('/playwright/voice-mobile.html');
  await startVoice(page);
  await page.waitForFunction(
    () => typeof (window as any).__grantPermission === 'function'
  );
  await page
    .getByRole('button', { name: 'Close assistant', exact: true })
    .click();
  await page.evaluate(() => (window as any).__grantPermission());
  await expect
    .poll(async () => (await readMockState(page)).trackStopped)
    .toBe(true);
  expect(await page.evaluate(() => (window as any).__permissionRequests)).toBe(
    1
  );
  expect(voice.sessionCreates).toHaveLength(1);
  await expect.poll(() => voice.sessionEnded).toBe(true);
  await page
    .getByRole('button', { name: 'Open AI Assistant', exact: true })
    .click();
  await expect(page.getByTestId('voice-start')).toBeVisible();
  expect(voice.sessionCreates).toHaveLength(1);
});

test('desktop record navigation keeps the same session and draft', async ({
  page
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  await page.goto('/playwright/voice-mobile.html');
  await startVoice(page);
  await page.getByLabel('Message draft').fill('Current draft');
  await page.getByRole('link', { name: 'Navigate background' }).click();
  await expect(page.getByText('Related record fixture')).toBeVisible();
  await expect(page.getByLabel('Message draft')).toHaveValue('Current draft');
  expect((await readMockState(page)).trackStopped).toBe(false);
  expect(voice.sessionCreates).toHaveLength(1);
  await page.getByRole('button', { name: 'Log out', exact: true }).click();
  await expect.poll(() => voice.sessionEnded).toBe(true);
  expect((await readMockState(page)).trackStopped).toBe(true);
});

test('mobile assistant makes background inert and restores it on close', async ({
  page
}) => {
  await page.setViewportSize({ width: 393, height: 851 });
  await installVoiceMocks(page);
  await page.goto('/playwright/voice-mobile.html');
  await expect(
    page.getByRole('dialog', { name: 'AI Assistant' })
  ).toBeVisible();
  await expect
    .poll(() =>
      page.getByTestId('background-app').evaluate((e) => !!e.closest('[inert]'))
    )
    .toBe(true);
  await page
    .getByRole('button', { name: 'Close assistant', exact: true })
    .click();
  await expect
    .poll(() =>
      page.getByTestId('background-app').evaluate((e) => !!e.closest('[inert]'))
    )
    .toBe(false);
});

test('mobile record navigation closes assistant and stops voice', async ({
  page
}) => {
  await page.setViewportSize({ width: 393, height: 851 });
  const voice = await installVoiceMocks(page, {
    capability: { ...defaultCapability, foreground_session: true }
  });
  await page.goto('/playwright/voice-mobile.html');
  await startVoice(page);
  await page.getByRole('link', { name: 'Open related record' }).click();
  await expect(page.getByText('Related record fixture')).toBeVisible();
  await expect(page.getByTestId('ai-chat-drawer')).not.toBeVisible();
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
