import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { revisionWork } from './scopeRevisionFixtures';
import {
  capturePanel,
  captureProcessSection,
  expectNoPageOverflow,
  mockExecutionWork,
  onlyReads,
  openExecutionPanel,
  panelRegion
} from './workExecutionFixtures';

nativeBaseline();
for (const scheme of ['light', 'dark'] as const) {
  test(`scope comparison retains versions and names missing dependency links (${scheme})`, async ({
    page
  }, info) => {
    const requests = await mockExecutionWork(page, revisionWork());
    await openExecutionPanel(page, 'plan', scheme);
    const plan = panelRegion(page, 'Plan & Safety');
    const review = plan.getByTestId('scope-revision-review');
    await expect(plan.getByTestId('scope-revision-notice')).toBeVisible();
    await expect(
      review.getByText('Latest current scope: version 2')
    ).toBeVisible();
    await expect(
      review.getByText('Superseded · reference only', { exact: true })
    ).toBeVisible();
    await expect(
      review.getByText('Affected records not recorded', { exact: true })
    ).toHaveCount(5);
    await expect(
      review.getByText(
        /linked revision reason and supporting evidence are not recorded/
      )
    ).toBeVisible();
    await expect(
      review.getByText('Revised text', { exact: true })
    ).toBeVisible();
    await expect(review.getByText('Same text', { exact: true })).toBeVisible();
    await expect(
      review.getByText('Removed text', { exact: true })
    ).toBeVisible();
    await expect(
      review.getByRole('button', {
        name: /Accept|Approve|Execute|Reassess all/
      })
    ).toHaveCount(0);
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`scope-comparison-${scheme}.png`));
    await review
      .getByRole('button', { name: 'Scope 1 → 2', exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath(`scope-comparison-${scheme}-viewport.png`),
      animations: 'disabled'
    });
    await review
      .getByRole('button', {
        name: 'Recorded investigation request revisions (1)'
      })
      .click();
    await expect(
      review.getByText('Synthetic first instrument', { exact: true })
    ).toBeVisible();
    await expect(
      review.getByText('Synthetic second instrument', { exact: true })
    ).toBeVisible();
    await expect(
      review.getByText('Retained prior evidence IDs: 501', { exact: true })
    ).toBeVisible();
    await expect(
      review
        .getByText('Linked source finding', { exact: true })
        .locator('..')
        .getByText('101', { exact: true })
    ).toBeVisible();
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`request-revision-${scheme}.png`));
    await review
      .getByText('Synthetic first instrument', { exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath(`request-revision-${scheme}-viewport.png`),
      animations: 'disabled'
    });
    await review
      .getByRole('link', { name: 'Open saved request 401 and evidence' })
      .click();
    await expect(
      panelRegion(page, 'Diagnose').getByTestId('investigation-request-401')
    ).toBeVisible();
    await expect(
      panelRegion(page, 'Diagnose').getByText(
        'Observation: Synthetic retained original observation'
      )
    ).toBeVisible();
    expect(onlyReads(requests)).toBe(true);
  });
}

test('revision notice follows Work, Diagnose, Parts and Verify without changing saved evidence', async ({
  page
}, info) => {
  const requests = await mockExecutionWork(page, revisionWork());
  await openExecutionPanel(page, 'work');
  for (const name of ['Work', 'Diagnose', 'Parts', 'Verify & Return']) {
    await page.getByRole('tab', { name, exact: true }).click();
    await expect(
      panelRegion(page, name).getByTestId('scope-revision-notice')
    ).toBeVisible();
    await expectNoPageOverflow(page);
  }
  await capturePanel(
    page,
    info.outputPath('revision-verification-context.png')
  );
  expect(onlyReads(requests)).toBe(true);
});

test('obsolete and conflicting scope records cannot be presented as effective', async ({
  page
}, info) => {
  for (const state of ['no_current_scope', 'conflicting_scope'] as const) {
    const overview = revisionWork();
    overview.prepared_package!.revision_review!.state = state;
    overview.prepared_package!.revision_review!.current_scope_id = null;
    overview.prepared_package!.scopes[0].current =
      state === 'conflicting_scope';
    overview.prepared_package!.scopes[1].current =
      state === 'conflicting_scope';
    await mockExecutionWork(page, overview);
    await openExecutionPanel(page, 'plan', 'dark');
    const plan = panelRegion(page, 'Plan & Safety');
    await expect(plan.getByTestId('scope-revision-notice')).toContainText(
      'Effective scope needs review'
    );
    await expect(
      plan.getByText(/Latest current scope:|Current scope version/)
    ).toHaveCount(0);
    await capturePanel(page, info.outputPath(`scope-${state}.png`));
  }
});

test('older server keeps saved versions and unavailable impact explicit', async ({
  page
}, info) => {
  const overview = revisionWork();
  delete overview.prepared_package!.revision_review;
  await mockExecutionWork(page, overview);
  await openExecutionPanel(page, 'plan');
  const review = panelRegion(page, 'Plan & Safety').getByTestId(
    'scope-revision-review'
  );
  await expect(
    review.getByText(/Revision comparisons are not supplied/)
  ).toBeVisible();
  await review
    .getByRole('button', { name: 'All retained scope versions (2)' })
    .click();
  await expect(review.getByText('Superseded · reference only')).toBeVisible();
  await capturePanel(page, info.outputPath('scope-older-server.png'));
  overview.prepared_package!.scopes[0].current = false;
  overview.prepared_package!.scopes[1].current = true;
  await page.reload();
  await expect(
    panelRegion(page, 'Plan & Safety').getByText(/Current scope version/)
  ).toHaveCount(0);
  await expect(
    panelRegion(page, 'Plan & Safety').getByText(
      /Current scope markers do not identify a single latest version/
    )
  ).toBeVisible();
});

for (const scheme of ['light', 'dark'] as const) {
  test(`O15 only bound affected dependencies need reassessment (${scheme})`, async ({
    page
  }, info) => {
    const work = revisionWork();
    work.prepared_package!.revision_review!.selective_reassessment = {
      rule: 'component_procedure_bindings.v1',
      changed_component_refs: ['MOTOR-DE-BRG'],
      changed_procedure_revision_ids: [],
      unmapped_changes: false,
      scope_execution: [
        { scope_id: 201, executable: false },
        { scope_id: 202, executable: true }
      ],
      items: [
        {
          kind: 'tasks',
          id: 'bearing-inspection',
          scope_id: 201,
          state: 'obsolete',
          executable: false,
          retained_evidence_ids: [501]
        },
        {
          kind: 'tasks',
          id: 'bearing-inspection',
          scope_id: 202,
          state: 'reassess',
          executable: false,
          retained_evidence_ids: [501]
        },
        {
          kind: 'materials',
          id: 'casing-gasket',
          scope_id: 202,
          state: 'retained',
          executable: true,
          retained_evidence_ids: [502]
        },
        {
          kind: 'criteria',
          id: 'bearing-test',
          scope_id: 202,
          state: 'reassess',
          executable: false,
          retained_evidence_ids: [503]
        },
        {
          kind: 'permissions',
          id: 'casing-access',
          scope_id: 202,
          state: 'retained',
          executable: true,
          retained_evidence_ids: [504]
        }
      ]
    };
    const requests = await mockExecutionWork(page, work);
    await openExecutionPanel(page, 'plan', scheme);
    const mapping = page.getByTestId('selective-reassessment');
    await expect(
      mapping.getByText('Reassessment required · not executable', {
        exact: true
      })
    ).toHaveCount(2);
    await expect(
      mapping.getByText('Evidence retained for this binding', { exact: true })
    ).toHaveCount(2);
    await expect(
      mapping.getByText('Obsolete scope · not executable', { exact: true })
    ).toBeVisible();
    expect(onlyReads(requests)).toBe(true);
    await expectNoPageOverflow(page);
    await captureProcessSection(
      page,
      mapping,
      info.outputPath(`o15-scope-mapping-${scheme}.png`)
    );
  });
}

for (const scheme of ['light', 'dark'] as const) {
  test(`review unmapped changes and obsolete-only bindings stay explicit (${scheme})`, async ({
    page
  }, info) => {
    const work = revisionWork();
    const review = work.prepared_package!.revision_review!;
    review.selective_reassessment = {
      rule: 'component_procedure_bindings.v1',
      changed_component_refs: ['MOTOR-DE-BRG'],
      changed_procedure_revision_ids: [11],
      unmapped_changes: true,
      scope_execution: [
        { scope_id: 201, executable: false },
        { scope_id: 202, executable: true }
      ],
      items: [1, 2].map((sequence) => ({
        kind: 'tasks',
        id: 'repeated-lockout',
        scope_id: 202,
        line_sequence: sequence,
        binding_index: 0,
        state: 'evidence_unknown' as const,
        executable: false,
        retained_evidence_ids: []
      }))
    };
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    await mockExecutionWork(page, work);
    await openExecutionPanel(page, 'plan', scheme, false, 'editor');
    const panel = page.getByTestId('scope-revision-review');
    await expect(page.getByTestId('unmapped-scope-change')).toBeVisible();
    await expect(
      panel.getByText(/Changed components: MOTOR-DE-BRG/)
    ).toBeVisible();
    await expect(
      panel.getByText('Evidence not established · not executable', {
        exact: true
      })
    ).toHaveCount(2);
    expect(
      errors.filter((message) => /same key|unique.*key/i.test(message))
    ).toEqual([]);
    await captureProcessSection(
      page,
      panel,
      info.outputPath(`review-scope-unmapped-${scheme}.png`)
    );
    review.selective_reassessment.items =
      review.selective_reassessment.items.map((row) => ({
        ...row,
        scope_id: 201,
        state: 'obsolete'
      }));
    await page.reload();
    await expect(page.getByTestId('scope-revision-notice')).toContainText(
      'dependency impact not recorded'
    );
    await expect(
      panel.getByText('Affected records not recorded', { exact: true })
    ).toHaveCount(5);
    await captureProcessSection(
      page,
      panel,
      info.outputPath(`review-scope-no-current-bindings-${scheme}.png`)
    );
  });
}
