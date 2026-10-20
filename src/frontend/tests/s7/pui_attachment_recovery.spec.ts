import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { closeoutWork, containment } from '../s6/closeoutFixtures';

nativeBaseline();
for (const { scheme, moved, missing } of [
  { scheme: 'light', moved: false, missing: false },
  { scheme: 'dark', moved: false, missing: false },
  { scheme: 'light', moved: true, missing: false },
  { scheme: 'light', moved: false, missing: true }
] as const) {
  test(`native attachment metadata ${missing ? 'missing revision' : moved ? 'owner moved' : 'conflict'} retains form and compares latest · ${scheme}`, async ({
    page
  }, info) => {
    await closeoutWork(page);
    let comment = 'Original attachment metadata';
    let revision = 'a'.repeat(64);
    let owner = 73;
    const bodies: any[] = [];
    const record = () => ({
      pk: 901,
      filename: 'Synthetic reference',
      attachment: null,
      link: 'https://example.invalid/reference',
      comment,
      model_type: 'workorder',
      model_id: owner,
      expected_revision: missing ? undefined : revision,
      file_size: 0,
      is_image: false,
      thumbnail: null,
      upload_date: '2026-10-19T08:00:00Z',
      upload_user: 3,
      user_detail: { pk: 3, username: 'Synthetic technician' },
      tags: []
    });
    await page.route('**/api/attachment/**', (route) => {
      const request = route.request();
      if (request.method() === 'OPTIONS')
        return route.fulfill({
          json: {
            name: 'Attachments',
            actions: {
              PUT: {
                link: { type: 'url', required: true, label: 'Link' },
                comment: { type: 'string', label: 'Comment' },
                model_type: { type: 'string' },
                model_id: { type: 'integer' },
                expected_revision: { type: 'string' }
              }
            }
          }
        });
      if (request.method() === 'PATCH') {
        bodies.push(request.postDataJSON());
        if (bodies.length === 1) {
          comment = 'Newer authorized metadata';
          revision = 'b'.repeat(64);
          if (moved) owner = 74;
          return route.fulfill({
            status: 409,
            json: { code: 'STALE_VERSION' }
          });
        }
        comment = bodies[1].comment;
        return route.fulfill({ json: record() });
      }
      if (/\/901\/$/.test(new URL(request.url()).pathname))
        return route.fulfill({ json: record() });
      return route.fulfill({
        json: { count: 1, results: [record()], next: null }
      });
    });
    await page.goto(
      `/playwright/s0-native.html?surface=maintenance-shell&sprint=s1&role=editor&work_access=reader&scheme=${scheme}&entry=${encodeURIComponent('/maintenance/work-orders/73/?work_panel=closeout')}`
    );
    await expect(page.getByTestId('prepared-closeout')).toBeVisible();
    await page
      .getByRole('button', { name: 'Attachments (1)', exact: true })
      .click();
    const row = page
      .getByRole('row')
      .filter({ hasText: 'Original attachment metadata' });
    await row.getByTestId(/row-action-menu/).click();
    await page.getByRole('menuitem', { name: 'Edit', exact: true }).click();
    const modal = page.getByRole('dialog', {
      name: 'Edit Attachment',
      exact: true
    });
    await expect(
      modal.getByRole('textbox', { name: 'text-field-comment', exact: true })
    ).toHaveValue(comment);
    await modal
      .getByRole('textbox', { name: 'text-field-comment', exact: true })
      .fill('My retained attachment draft');
    if (missing) {
      await expect(
        modal.getByText('Attachment revision unavailable', { exact: true })
      ).toBeVisible();
      await expect(
        modal.getByRole('button', { name: 'Submit', exact: true })
      ).toBeDisabled();
      await expect(
        modal.getByRole('textbox', { name: 'text-field-comment', exact: true })
      ).toHaveValue('My retained attachment draft');
      await modal
        .getByRole('textbox', { name: 'text-field-comment', exact: true })
        .press('Control+Enter');
      expect(bodies).toHaveLength(0);
      return;
    }
    // Exercise a real background query refresh while the modal owns a draft.
    await page
      .locator('button')
      .filter({ hasText: 'Refresh fixture' })
      .evaluate((button: HTMLButtonElement) => button.click());
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
    await expect(
      modal.getByRole('textbox', { name: 'text-field-comment', exact: true })
    ).toHaveValue('My retained attachment draft');
    await modal.getByRole('button', { name: 'Submit', exact: true }).click();
    const recovery = modal.getByTestId('draft-recovery');
    await expect(recovery).toContainText('My retained attachment draft');
    await expect(
      page.getByText('Invalid Return Code', { exact: true })
    ).toHaveCount(0);
    await expect(
      modal.getByRole('textbox', { name: 'text-field-comment', exact: true })
    ).toHaveValue('My retained attachment draft');
    await expect(
      modal.getByRole('button', { name: 'Submit', exact: true })
    ).toBeDisabled();
    await modal
      .getByRole('textbox', { name: 'text-field-comment', exact: true })
      .press('Control+Enter');
    await recovery
      .getByRole('button', { name: 'Review latest record', exact: true })
      .click();
    if (moved) {
      await expect(recovery).toContainText('Latest metadata unavailable');
      await expect(recovery).not.toContainText('Newer authorized metadata');
      await expect(
        recovery.getByRole('button', { name: 'Keep draft on reviewed version' })
      ).toHaveCount(0);
      expect(bodies).toHaveLength(1);
      return;
    }
    await expect(recovery).toContainText('Newer authorized metadata');
    expect(bodies).toHaveLength(1);
    await containment(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`attachment-conflict-${scheme}.png`),
      fullPage: true
    });
    await recovery
      .getByRole('button', {
        name: 'Keep draft on reviewed version',
        exact: true
      })
      .click();
    await modal.getByRole('button', { name: 'Submit', exact: true }).click();
    await expect(modal).not.toBeVisible();
    expect(bodies).toHaveLength(2);
    expect(bodies[0].expected_revision).toBe('a'.repeat(64));
    expect(bodies[1]).toMatchObject({
      comment: 'My retained attachment draft',
      expected_revision: 'b'.repeat(64)
    });
  });
}

test('native attachment create retains metadata on refusal and locks an uncertain append', async ({
  page
}, info) => {
  await closeoutWork(page);
  const bodies: any[] = [];
  await page.route('**/api/attachment/**', (route) => {
    const method = route.request().method();
    if (method === 'OPTIONS')
      return route.fulfill({
        json: {
          name: 'Attachments',
          actions: {
            POST: {
              link: { type: 'url', required: true, label: 'Link' },
              comment: { type: 'string', label: 'Comment' },
              model_type: { type: 'string' },
              model_id: { type: 'integer' }
            }
          }
        }
      });
    if (method === 'POST') {
      bodies.push(route.request().postDataJSON());
      return bodies.length === 1
        ? route.fulfill({
            status: 400,
            json: { comment: ['Synthetic metadata refused'] }
          })
        : route.abort('failed');
    }
    return route.fulfill({ json: { count: 0, results: [], next: null } });
  });
  await page.goto(
    `/playwright/s0-native.html?surface=maintenance-shell&sprint=s1&role=editor&work_access=reader&entry=${encodeURIComponent('/maintenance/work-orders/73/?work_panel=closeout')}`
  );
  await page
    .getByRole('button', { name: 'Attachments (0)', exact: true })
    .click();
  await page
    .getByRole('button', {
      name: 'action-button-add-external-link',
      exact: true
    })
    .click();
  const modal = page.getByRole('dialog', {
    name: 'Upload Attachment',
    exact: true
  });
  await modal
    .getByRole('textbox', { name: 'text-field-link', exact: true })
    .fill('https://example.invalid/retained-reference');
  const comment = modal.getByRole('textbox', {
    name: 'text-field-comment',
    exact: true
  });
  await comment.fill('Retained new attachment metadata');
  await modal.getByRole('button', { name: 'Submit', exact: true }).click();
  await expect(
    modal.getByText('Attachment save refused.', { exact: false })
  ).toBeVisible();
  await expect(comment).toHaveValue('Retained new attachment metadata');
  await comment.fill('Corrected new attachment metadata');
  await page
    .locator('button')
    .filter({ hasText: 'Refresh fixture' })
    .evaluate((button: HTMLButtonElement) => button.click());
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  await expect(comment).toHaveValue('Corrected new attachment metadata');
  await modal.getByRole('button', { name: 'Submit', exact: true }).click();
  await expect(
    modal.getByText('Attachment save not confirmed.', { exact: false })
  ).toBeVisible();
  await expect(comment).toHaveValue('Corrected new attachment metadata');
  await expect(
    modal.getByRole('button', { name: 'Submit', exact: true })
  ).toBeDisabled();
  await comment.press('Control+Enter');
  await containment(page);
  await page.screenshot({
    path: info.outputPath('attachment-create-uncertain.png')
  });
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toMatchObject({
    model_type: 'workorder',
    model_id: 73,
    comment: 'Corrected new attachment metadata'
  });
});
