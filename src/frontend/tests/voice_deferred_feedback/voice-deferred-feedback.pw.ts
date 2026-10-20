/*
 * Mounted-component evidence for VOICE-H01/H02/H05 (real surfaces, real
 * stores, real browser). Run only via playwright.voice-deferred-feedback.config.ts:
 *   npx playwright test -c playwright.voice-deferred-feedback.config.ts
 */
import { expect, test } from '@playwright/test';

declare global {
  interface Window {
    __voiceFixture: {
      setClientState: (state: string) => void;
      setError: (error: unknown) => void;
      micCount: () => number;
      surface: {
        getState: () => {
          consent: boolean;
          requestStart: () => void;
          setStartBlockedReason: (reason: string | null) => void;
        };
        setState: (partial: Record<string, unknown>) => void;
      };
      session: { setState: (partial: Record<string, unknown>) => void };
      consentSnapshot: () => Record<string, unknown>;
    };
  }
}

test.beforeEach(async ({ page }) => {
  await page.goto('/tests/voice_deferred_feedback/index.html');
  await expect(page.getByTestId('voice-session-control').first()).toBeVisible();
});

test('H01: every surface maps error codes to translated text, never raw codes or server detail', async ({
  page
}) => {
  await page.evaluate(() =>
    window.__voiceFixture.setError({
      code: 'VOICE_SIGNALING_FAILED',
      detail: 'raw-server-detail-xyz'
    })
  );
  const errors = page.getByTestId('voice-error');
  await expect(errors.first()).toBeVisible();
  const texts = await errors.allTextContents();
  expect(texts.length).toBeGreaterThanOrEqual(2);
  for (const text of texts) {
    expect(text).toContain("can't reach the speech service");
    expect(text.toLowerCase()).toContain('try again');
    expect(text).not.toContain('VOICE_SIGNALING_FAILED');
    expect(text).not.toContain('raw-server-detail-xyz');
  }

  await page.evaluate(() =>
    window.__voiceFixture.setError({
      code: 'SOME_FUTURE_SERVER_CODE',
      detail: 'raw-server-detail-xyz'
    })
  );
  const generic = await page.getByTestId('voice-error').allTextContents();
  expect(generic.length).toBeGreaterThanOrEqual(2);
  for (const text of generic) {
    expect(text).toContain('Something went wrong with voice');
    expect(text).not.toContain('SOME_FUTURE_SERVER_CODE');
    expect(text).not.toContain('raw-server-detail-xyz');
  }
});

test('H02: a mismatched consent submit stays open with a visible accessible refusal and no side effects', async ({
  page
}) => {
  const before = await page.evaluate(() => {
    const f = window.__voiceFixture;
    f.session.setState({
      capability: {
        enabled: true,
        voices: [{ locale: 'en-US', voice: 'aria' }],
        consent_version: 'consent-v2',
        idle_timeout_s: 300,
        foreground_session: false
      }
    });
    f.surface.setState({ consent: true });
    return f.consentSnapshot();
  });
  await expect(page.getByRole('dialog')).toBeVisible();
  await page
    .getByLabel('I have read the disclosure and want to start voice.')
    .check();
  await page.getByTestId('voice-consent-start').click();

  const issue = page.getByTestId('voice-consent-issue');
  await expect(issue).toBeVisible();
  await expect(issue).toHaveAttribute('role', 'alert');
  await expect(issue).toContainText(/disclosure/i);
  // Invalid submit must not close the dialog.
  await expect(page.getByRole('dialog')).toBeVisible();
  const after = await page.evaluate(() => {
    const f = window.__voiceFixture;
    return {
      mic: f.micCount(),
      snapshot: f.consentSnapshot(),
      consent: f.surface.getState().consent
    };
  });
  // ... must not start the microphone ...
  expect(after.mic).toBe(0);
  // ... and must not store the consented voice preferences.
  expect(after.snapshot).toEqual(before);
  expect(after.consent).toBe(true);

  // Recovery is explicit and safe: rechecking keeps the refusal while the
  // capability seam still reports the mismatch, and never starts anything.
  await page.getByTestId('voice-consent-recheck').click();
  await expect(issue).toBeVisible();
  expect(await page.evaluate(() => window.__voiceFixture.micCount())).toBe(0);
});

test('H05: a refused start renders an accessible explanation with safe fallback and a gate-respecting retry', async ({
  page
}) => {
  // Existing eligibility reason label is used verbatim.
  await page.evaluate(() => {
    const f = window.__voiceFixture;
    f.surface.getState().setStartBlockedReason('turn-in-flight');
    f.surface.getState().requestStart();
  });
  const feedback = page.getByTestId('voice-start-feedback').first();
  await expect(feedback).toBeVisible();
  await expect(feedback).toContainText(
    'Voice cannot start while a message is being processed.'
  );
  await expect(feedback.getByRole('status')).toBeVisible();
  expect(
    await page.evaluate(() => window.__voiceFixture.surface.getState().consent)
  ).toBe(false);
  expect(await page.evaluate(() => window.__voiceFixture.micCount())).toBe(0);

  // Unknown eligibility reason gets the safe fallback, never the raw reason.
  await page.evaluate(() => {
    const f = window.__voiceFixture;
    f.surface.getState().setStartBlockedReason('brand-new-reason');
    f.surface.getState().requestStart();
  });
  await expect(page.getByTestId('voice-start-feedback').first()).toContainText(
    "Voice can't start right now"
  );
  const fbText = await page
    .getByTestId('voice-start-feedback')
    .first()
    .textContent();
  expect(fbText).not.toContain('brand-new-reason');

  // Capability gap is explained; retry re-runs the gates and only then opens
  // consent — no direct controller/mic start, no consent bypass.
  await page.evaluate(() => {
    const f = window.__voiceFixture;
    f.surface.getState().setStartBlockedReason(null);
    f.session.setState({ capability: null });
    f.surface.getState().requestStart();
  });
  await expect(page.getByTestId('voice-start-feedback').first()).toContainText(
    /Voice isn't ready|still loading/
  );
  expect(
    await page.evaluate(() => window.__voiceFixture.surface.getState().consent)
  ).toBe(false);
  await page.evaluate(() =>
    window.__voiceFixture.session.setState({
      // Real server capabilities always carry `voices`; the dialog renders
      // `capability?.voices.map(...)`, so the fixture must include it too.
      capability: {
        enabled: true,
        voices: [],
        runtime: { available: true }
      }
    })
  );
  await page.getByTestId('voice-start-feedback-retry').first().click();
  expect(
    await page.evaluate(() => window.__voiceFixture.surface.getState().consent)
  ).toBe(true);
  await expect(page.getByTestId('voice-start-feedback')).toHaveCount(0);
  expect(await page.evaluate(() => window.__voiceFixture.micCount())).toBe(0);
});
