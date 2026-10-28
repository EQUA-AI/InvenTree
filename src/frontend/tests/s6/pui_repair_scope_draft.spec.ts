import { expect, test } from '@playwright/test';
import { auditAccessibility } from '../s0/accessibility';
import { assertReadableContrast } from '../s0/contrast';
import { nativeBaseline } from '../s0/guards';
import {
  captureProcessSection,
  expectNoPageOverflow,
  onlyReads,
  openExecutionPanel
} from '../s4/workExecutionFixtures';
import { mockScopeDraft, scopeResponse } from './repairScopeDraftFixtures';

nativeBaseline();

for (const scheme of ['light', 'dark'] as const) {
  test(`saved proposal retains receipt, sources and pending authority (${scheme})`, async ({
    page
  }, info) => {
    const { requests, reads } = await mockScopeDraft(page);
    await openExecutionPanel(page, 'plan', scheme);
    const proposal = page.getByTestId('repair-scope-draft');
    await expect(proposal).toContainText('Draft #804 · Revision 2');
    await expect(proposal).toContainText('User 9');
    await expect(proposal).toContainText('28 Oct 2026');
    await expect(proposal).toContainText('Proposed · Review pending');
    await expect(proposal).toContainText(
      'loading and sensor error remain alternatives'
    );
    for (const title of [
      'Scope',
      'Tools and supplies',
      'Required PPE',
      'Isolation checks',
      'Post-work checks',
      'Supporting sources',
      'Conditional parts decision',
      'Open questions'
    ]) {
      await expect(
        proposal.getByRole('heading', { name: title, exact: true })
      ).toBeVisible();
    }
    await expect(
      proposal.getByRole('link', { name: 'Source event #92' })
    ).toHaveAttribute('href', '/machines/machine/18/health/?anomaly=92');
    await expect(
      proposal.getByRole('link', { name: 'Repair packet #901' })
    ).toHaveAttribute('href', '/repair/packets/901/');
    await expect(proposal.getByTestId('repair-scope-sources')).toContainText(
      'P08-TEMP-E03 · Prepared inspection evidence · Attachment #503'
    );
    await expect(proposal.getByTestId('repair-scope-sources')).toContainText(
      'related-motor-reference.pdf · Attachment #504'
    );
    await expect(proposal.getByTestId('repair-scope-sources')).toContainText(
      'Applicability to the fitted equipment remains unverified.'
    );
    await expect(proposal.getByTestId('repair-scope-parts')).toContainText(
      '0 replacement parts'
    );
    await expect(proposal.getByTestId('repair-scope-parts')).toContainText(
      'only if inspection identifies damage'
    );
    for (const gate of [
      'Supervisor review: pending',
      'Site safety authorization: pending',
      'Physical execution: not started',
      'Post-repair verification: not started'
    ]) {
      await expect(
        proposal.getByTestId('repair-scope-pending-gates')
      ).toContainText(gate);
    }
    await expect(proposal.getByRole('button')).toHaveCount(0);
    await assertReadableContrast(
      proposal.getByTestId('repair-scope-draft-identity')
    );
    await assertReadableContrast(
      proposal.getByText('Proposed · Review pending', { exact: true })
    );
    await assertReadableContrast(
      proposal.getByText('Supervisor review: pending', { exact: true })
    );
    await expect(
      page.getByText('No blocking gates', { exact: true })
    ).toHaveCount(0);
    await expect(
      page.getByText('Gate readiness not evaluated', { exact: true })
    ).toBeVisible();
    await expectNoPageOverflow(page);
    await captureProcessSection(
      page,
      proposal,
      info.outputPath(`proposed-scope-${scheme}.png`)
    );
    await auditAccessibility(page, proposal, info, `proposed-scope-${scheme}`);
    await page.reload();
    await expect(page.getByTestId('repair-scope-draft-identity')).toHaveText(
      'Draft #804 · Revision 2'
    );
    expect(reads.length).toBeGreaterThanOrEqual(2);
    expect(reads.every((method) => method === 'GET')).toBe(true);
    expect(onlyReads(requests)).toBe(true);
  });
}

test('no saved draft does not invent a proposal', async ({ page }) => {
  const response = scopeResponse();
  response.draft = null;
  const { reads } = await mockScopeDraft(page, response);
  await openExecutionPanel(page, 'plan');
  await expect.poll(() => reads.length).toBeGreaterThan(0);
  await expect(page.getByTestId('repair-scope-draft')).toHaveCount(0);
  await expect(
    page.getByText('Proposed repair scope unavailable', { exact: true })
  ).toHaveCount(0);
});

test('a newly persisted draft is read on the active pane and polling stops when hidden', async ({
  page
}) => {
  const initial = scopeResponse();
  initial.draft = null;
  const { requests } = await mockScopeDraft(page, initial);
  let response = initial;
  let reads = 0;
  await page.route(
    '**/api/tasks/work-orders/73/repair-scope-draft/',
    (route) => {
      reads += 1;
      return route.fulfill({ json: response });
    }
  );
  await openExecutionPanel(page, 'plan');
  await expect.poll(() => reads).toBeGreaterThan(0);
  await expect(page.getByTestId('repair-scope-draft')).toHaveCount(0);
  response = scopeResponse();
  await expect(page.getByTestId('repair-scope-draft-identity')).toHaveText(
    'Draft #804 · Revision 2',
    { timeout: 8000 }
  );
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  const readsWhenHidden = reads;
  await page.waitForTimeout(5500);
  expect(reads).toBe(readsWhenHidden);
  expect(onlyReads(requests)).toBe(true);
});

for (const status of [403, 503]) {
  test(`unavailable draft ${status} offers only a read retry`, async ({
    page
  }) => {
    const { reads, requests } = await mockScopeDraft(
      page,
      { detail: 'Synthetic read refused' },
      status
    );
    await openExecutionPanel(page, 'plan');
    await expect(
      page.getByText('Proposed repair scope unavailable', { exact: true })
    ).toBeVisible();
    await expect(page.getByTestId('repair-scope-draft')).toHaveCount(0);
    const initialReads = reads.length;
    await page.getByRole('button', { name: 'Reload proposed scope' }).click();
    await expect.poll(() => reads.length).toBeGreaterThan(initialReads);
    expect(onlyReads(requests)).toBe(true);
  });
}

for (const invalid of [
  'different work',
  'unknown parts',
  'unprepared evidence'
] as const) {
  test(`unverified proposal is withheld: ${invalid}`, async ({ page }) => {
    const response = scopeResponse();
    if (invalid === 'different work') response.draft!.work_order_id = 74;
    if (invalid === 'unknown parts')
      Object.assign(response.draft!.scope, { replacement_parts: null });
    if (invalid === 'unprepared evidence')
      Object.assign(response.draft!.sources.e03, { prepared: false });
    await mockScopeDraft(page, response);
    await openExecutionPanel(page, 'plan');
    await expect(
      page.getByText('Proposed repair scope unavailable', { exact: true })
    ).toBeVisible();
    await expect(page.getByTestId('repair-scope-draft')).toHaveCount(0);
    await expect(
      page.getByText('0 replacement parts', { exact: true })
    ).toHaveCount(0);
  });
}

test('a changed work revision retains the saved receipt and warns to review', async ({
  page
}) => {
  const response = scopeResponse();
  response.lifecycle_version = 8;
  await mockScopeDraft(page, response);
  await openExecutionPanel(page, 'plan');
  await expect(page.getByTestId('repair-scope-draft-identity')).toHaveText(
    'Draft #804 · Revision 2'
  );
  await expect(
    page.getByText('Work order changed after this draft', { exact: true })
  ).toBeVisible();
});
