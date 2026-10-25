/** Local mocked-API overlay matrix: native keyboard and focus ownership. */
import { type Page, expect, test } from '@playwright/test';
import { loadDrawerFixture, prepareDrawerFixture } from './ai_drawer_harness';
import { threadId } from './aichat_harness';

async function setup(page: Page) {
  await page.setViewportSize({ width: 390, height: 740 });
  await prepareDrawerFixture(page, {
    voice: false,
    foundation: {
      threadDetailMessages: [
        {
          id: 'source-answer',
          role: 'assistant',
          content: 'Recorded source answer.',
          timestamp: '2026-10-24T00:00:00Z',
          media_evidence: [
            {
              attachment_id: 19,
              segment_index: 0,
              media_type: 'document',
              model_type: 'assetmachine',
              model_id: 7,
              label: 'Recorded source',
              source_revision: 'snapshot-1'
            }
          ]
        }
      ]
    }
  });
  await page.route('**/api/ai/threads/*/memory', (route) =>
    route.fulfill({ status: 404, json: { detail: 'not available' } })
  );
  await page.route('**/api/aichat/evidence/media/**', (route) =>
    route.fulfill({ status: 404, json: { detail: 'not available' } })
  );
  await loadDrawerFixture(page);
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: 'Server conversation' })
    .click();
}
async function assertTop(page: Page, title: string) {
  const dialog = page.getByRole('dialog', { name: title, exact: true });
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate((element) => !!element.closest('[inert]'))).toBe(
    false
  );
  for (const key of ['Tab', 'Tab', 'Shift+Tab', 'Tab']) {
    await page.keyboard.press(key);
    expect(
      await dialog.evaluate((element) =>
        element.contains(document.activeElement)
      )
    ).toBe(true);
  }
  await expect(page.getByTestId('ai-chat-drawer')).toHaveAttribute('inert', '');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId('ai-chat-drawer')).toHaveAttribute(
    'aria-modal',
    'true'
  );
  await expect(
    page.getByRole('textbox', { name: 'Message', exact: true })
  ).toBeEditable();
}
for (const [action, title] of [
  ['rename', 'Rename conversation'],
  ['share', 'Share conversation'],
  ['delete', 'Delete conversation?'],
  ['memory', 'What this chat remembers']
] as const) {
  test(`${action} owns the mobile dialog and Escape restores a usable assistant`, async ({
    page
  }) => {
    await setup(page);
    await page.getByTestId('select-ai-chat-thread').click();
    await page.getByTestId(`${action}-ai-chat-thread-${threadId}`).click();
    await assertTop(page, title);
  });
}
test('export owns the mobile dialog and returns to its invoking menu control', async ({
  page
}) => {
  await setup(page);
  await page.getByTestId('select-ai-chat-thread').click();
  await page.getByTestId('export-owned-ai-chat-threads').click();
  await assertTop(page, 'Export my saved conversations');
});
test('an unavailable global source still owns the modal and returns focus to its citation', async ({
  page
}) => {
  await setup(page);
  const citation = page.getByTestId('evidence-chip-19:0');
  await citation.click();
  await expect(page.getByTestId('media-evidence-unavailable')).toBeVisible();
  await assertTop(page, 'Recorded source');
  await expect(citation).toBeFocused();
});
