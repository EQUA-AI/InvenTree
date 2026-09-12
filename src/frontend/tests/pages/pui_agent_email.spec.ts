import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
import { mockChatFoundation, openChat } from './aichat_harness.js';

const accountId = '3b07ab75-90a1-4c14-9c36-14203ad03391';
const approvalId = '1d07ab75-90a1-4c14-9c36-14203ad03391';
const mailbox = {
  id: accountId,
  name: 'Recording mailbox',
  address: 'sender@example.test',
  provider: 'smtp_imap',
  enabled: true,
  send_enabled: true,
  receive_enabled: true,
  verified_send: true,
  verified_receive: true,
  health: 'ready',
  sync: [],
  permissions: { read: true, draft: true, send: true, admin: true }
};

test('mail draft requires complete review and displays accepted submission', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  let payload: Record<string, unknown> = {};
  let status = 'pending';
  const actions: string[] = [];
  const approval = () => ({
    id: approvalId,
    status,
    action_type: 'email',
    summary: 'Email review fixture',
    risk_tier: 2,
    payload,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    execution_result:
      status === 'succeeded'
        ? { execution_state: 'succeeded', receipt_id: 'recording-receipt' }
        : null
  });
  await page.route('**/api/aichat/email/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/accounts/'))
      return route.fulfill({ json: { results: [mailbox] } });
    if (url.pathname.endsWith('/messages/'))
      return route.fulfill({ json: { results: [] } });
    if (url.pathname.endsWith('/drafts/')) {
      payload = {
        ...route.request().postDataJSON(),
        _mailbox: { account_id: accountId },
        sender: mailbox.address
      };
      return route.fulfill({ status: 201, json: approval() });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.route('**/api/approvals/**', async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === 'POST') {
      actions.push(url.pathname.split('/').at(-2) ?? '');
      if (url.pathname.endsWith('/approve/')) status = 'succeeded';
      return route.fulfill({ json: approval() });
    }
    if (url.pathname.endsWith('/card-package/'))
      return route.fulfill({
        json: {
          ...approval(),
          current_revision_number: 0,
          review_hash: 'review-v1',
          review_sections: [
            {
              id: 'sender',
              label: 'From',
              text: mailbox.address,
              required: true
            },
            {
              id: 'body',
              label: 'Full message',
              text: payload.body,
              required: true
            }
          ]
        }
      });
    if (url.pathname.includes(approvalId))
      return route.fulfill({ json: approval() });
    if (url.pathname.endsWith('/count/'))
      return route.fulfill({ json: { count: 1 } });
    return route.fulfill({
      json: { results: Object.keys(payload).length ? [approval()] : [] }
    });
  });
  await page.goto('/web/home');
  await openChat(page);
  await page.getByRole('tab', { name: 'Mail', exact: true }).click();
  await page.getByLabel('Mailbox', { exact: true }).click();
  await page.getByRole('option', { name: /Recording mailbox/ }).click();
  await page
    .getByRole('button', { name: 'Compose email', exact: true })
    .click();
  await page.getByLabel('To', { exact: true }).fill('recipient@example.test');
  await page.getByLabel('BCC', { exact: true }).fill('hidden@example.test');
  await page.getByLabel('Subject', { exact: true }).fill('Recording only');
  await page.getByLabel('Message', { exact: true }).fill('Reviewed body');
  await page.getByRole('button', { name: 'Create draft for review' }).click();
  await page.getByText('Email review fixture', { exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Approve email submission' })
  ).toBeDisabled();
  await page
    .getByLabel(
      'I have reviewed the sender, every recipient, full message and attachments.'
    )
    .check();
  await page.getByRole('button', { name: 'Approve email submission' }).click();
  await expect(
    page.getByText(
      'Accepted by the mail provider. Recipient delivery is not confirmed.'
    )
  ).toBeVisible();
  expect(actions).toEqual(['open', 'confirm-viewed', 'approve']);
  expect(payload.bcc).toBe('hidden@example.test');
});

test('disabled mail has no compose or send controls', async ({ browser }) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  await page.route('**/api/aichat/email/**', (route) =>
    route.fulfill({ status: 404, json: { error: 'feature_disabled' } })
  );
  await page.goto('/web/home');
  await openChat(page);
  await page.getByRole('tab', { name: 'Mail', exact: true }).click();
  await expect(
    page.getByText('Agent mail is unavailable or disabled.')
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Compose email' })).toHaveCount(
    0
  );
});
