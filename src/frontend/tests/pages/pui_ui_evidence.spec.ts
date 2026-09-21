import { expect, test } from '@playwright/test';
import { installVoiceMocks, readMockState, startVoice } from './voice_harness';

const revision = 'a'.repeat(64);
test.beforeEach(async ({ page }) => {
  await page.route('**/*', (route) =>
    ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort()
  );
  await page.route('**/api/aichat/evidence/media/9/metadata/**', (route) =>
    route.fulfill({
      json: {
        revision,
        content_type: 'application/pdf',
        page_count: 2,
        page_labels: ['i', '1']
      }
    })
  );
  // These tests assert the authorized URL/locator contract, not native PDF rendering.
  await page.route('**/api/aichat/evidence/media/9/?*', (route) =>
    route.fulfill({
      contentType: 'application/pdf',
      body: '%PDF-1.4\n%%EOF'
    })
  );
});

test('evidence stops voice, gives one dialog ownership, and restores citation focus', async ({
  page
}) => {
  const voice = await installVoiceMocks(page);
  await page.goto('/playwright/voice-mobile.html');
  await startVoice(page);
  const citation = page.getByRole('button', {
    name: 'Evidence page 2',
    exact: true
  });
  await citation.click();
  const viewer = page.getByTestId('media-evidence-modal');
  await expect(viewer).toContainText('Page 2 of 2');
  await expect(page.locator('object')).toHaveAttribute(
    'data',
    new RegExp(`revision=${revision}#page=2`)
  );
  await expect.poll(() => voice.sessionEnded).toBe(true);
  expect((await readMockState(page)).trackStopped).toBe(true);
  await expect(page.getByTestId('ai-chat-drawer')).toHaveAttribute('inert', '');
  await expect
    .poll(() => page.locator('#root').evaluate((element) => element.inert))
    .toBe(true);
  await expect(page.locator('[role="dialog"][aria-modal="true"]')).toHaveCount(
    1
  );
  await page.keyboard.press('Escape');
  await expect(viewer).toHaveCount(0);
  await expect(citation).toBeFocused();
  expect(voice.sessionCreates).toHaveLength(1);
});

test('changing page of the same revision resets the document locator', async ({
  page
}) => {
  await installVoiceMocks(page);
  await page.goto('/playwright/voice-mobile.html');
  await page
    .getByRole('button', { name: 'Evidence page 1', exact: true })
    .click();
  await expect(page.getByTestId('media-evidence-modal')).toContainText(
    'Page 1 of 2'
  );
  await page.evaluate(() =>
    window.dispatchEvent(new Event('fixture-evidence-next'))
  );
  await expect(page.getByTestId('media-evidence-modal')).toContainText(
    'Page 2 of 2'
  );
  await expect(page.locator('object')).toHaveAttribute('data', /#page=2/);
});

test('missing cited revision is unavailable and never fetches replacement bytes', async ({
  page
}) => {
  await installVoiceMocks(page);
  const bytes: string[] = [];
  page.on('request', (request) => {
    if (/\/media\/9\/\?/.test(request.url())) bytes.push(request.url());
  });
  await page.route('**/api/aichat/evidence/media/9/metadata/**', (route) =>
    route.fulfill({ status: 404, json: {} })
  );
  await page.goto('/playwright/voice-mobile.html');
  await page
    .getByRole('button', { name: 'Evidence page 1', exact: true })
    .click();
  await expect(page.getByTestId('media-evidence-unavailable')).toBeVisible();
  expect(bytes).toEqual([]);
  await expect(
    page.getByRole('link', { name: 'Open original', exact: true })
  ).toHaveCount(0);
});

test('invalid page is explicitly source-level and uses no fabricated exact locator', async ({
  page
}) => {
  await installVoiceMocks(page);
  await page.route('**/api/aichat/evidence/media/9/metadata/**', (route) =>
    route.fulfill({
      json: {
        revision,
        content_type: 'application/pdf',
        page_count: 1,
        page_labels: ['1']
      }
    })
  );
  await page.goto('/playwright/voice-mobile.html');
  await page
    .getByRole('button', { name: 'Evidence page 2', exact: true })
    .click();
  await expect(page.getByTestId('media-evidence-modal')).toContainText(
    'Exact page unavailable'
  );
  await expect(
    page.getByRole('link', { name: 'Open original', exact: true })
  ).toHaveAttribute('href', new RegExp(`revision=${revision}$`));
});
