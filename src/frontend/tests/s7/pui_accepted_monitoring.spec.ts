import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  accepted,
  closeoutWork,
  containment,
  openCloseout
} from '../s6/closeoutFixtures';

nativeBaseline();

for (const scheme of ['light', 'dark'] as const) {
  test(`O15 monitoring links the exact immutable report revision (${scheme})`, async ({
    page
  }, info) => {
    const { setOverview, commands } = await closeoutWork(page, {
      completed: true
    });
    const reports = [1, 2].map((revision) => ({
      id: 600 + revision,
      revision,
      url: `/api/tasks/work-orders/73/closeout/reports/${600 + revision}/`,
      content_hash: String(revision).repeat(64),
      completed_by: 3,
      completed_by_name: 'Synthetic Technician',
      completed_at: accepted!.completed_at,
      frozen_by: revision === 2 ? 4 : 3,
      frozen_by_name:
        revision === 2 ? 'Synthetic Supervisor' : 'Synthetic Technician',
      frozen_at:
        revision === 2 ? '2026-10-19T10:05:00Z' : accepted!.completed_at,
      verified_by: null,
      verified_by_name: null,
      verified_at: null,
      amendment_id: revision === 2 ? 40 : null
    }));
    setOverview({
      machine: null,
      structured_closeout: { ...accepted!, reports }
    });
    await page.route('**/closeout/reports/*/', (route) => {
      const id = Number(
        new URL(route.request().url()).pathname.split('/').at(-2)
      );
      const report = reports.find((row) => row.id === id)!;
      return route.fulfill({
        json: {
          ...report,
          text: `Synthetic frozen monitoring account ${report.revision}`,
          document: { synthetic: true, revision: report.revision },
          canonical_export: JSON.stringify({
            document: { revision: report.revision, synthetic: true },
            text: `Synthetic frozen monitoring account ${report.revision}`
          })
        }
      });
    });
    await openCloseout(page, scheme);
    const panel = page.getByRole('tabpanel', { name: 'Closeout', exact: true });
    const monitoring = panel.getByTestId('monitoring-continuity');
    await monitoring.locator('summary').click();
    await expect(monitoring).toContainText('no machine is linked to this Work');
    await containment(page);
    await monitoring.evaluate((element) =>
      window.scrollTo(
        0,
        element.getBoundingClientRect().top + window.scrollY - 180
      )
    );
    await page.screenshot({
      animations: 'disabled',
      path: info.outputPath(`monitoring-snapshot-links-${scheme}.png`),
      fullPage: false
    });
    const revisionLink = monitoring.getByRole('link', {
      name: 'Frozen report · Revision 2',
      exact: true
    });
    if ((page.viewportSize()?.width ?? 1280) <= 390) {
      await revisionLink.scrollIntoViewIfNeeded();
      await page.screenshot({
        animations: 'disabled',
        path: info.outputPath(`monitoring-snapshot-link-target-${scheme}.png`)
      });
    }
    await revisionLink.click();
    await expect(page.getByTestId('destination')).toContainText('report=602');
    const frozen = panel.getByTestId('accepted-report');
    await frozen.getByText('Read frozen report', { exact: true }).click();
    await expect(frozen).toContainText('Synthetic frozen monitoring account 2');
    await expect(frozen).toContainText('2'.repeat(64));
    expect(commands).toEqual([]);
  });
}
