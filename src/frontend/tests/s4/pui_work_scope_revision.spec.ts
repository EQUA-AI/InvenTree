import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { revisionWork } from './scopeRevisionFixtures';
import {
  capturePanel,
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
