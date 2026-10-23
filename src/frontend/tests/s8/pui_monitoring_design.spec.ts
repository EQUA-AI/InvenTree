import { test } from '@playwright/test';
import { NOW as PERFORMANCE_NOW } from '../performance/fixtures';
import { nativeBaseline } from '../s0/guards';
import { openHealth } from '../s3/healthFixtures';
import { captureState } from './hyperagentEvidence';
import { openPausedPerformance } from './monitoringFixtures';

nativeBaseline();
const phase = process.env.AIMMS_CAPTURE_PHASE || 'final';
for (const screen of ['health', 'performance'] as const) {
  for (const scheme of ['light', 'dark'] as const) {
    test(`${screen} responsive design matrix ${scheme}`, async ({ page }) => {
      test.setTimeout(180_000);
      if (screen === 'health')
        await openHealth(page, {
          scheme,
          nativeTheme: true,
          compactChrome: true
        });
      else {
        await page.clock.setFixedTime(new Date(PERFORMANCE_NOW));
        await openPausedPerformance(page, {
          scheme,
          harness: 's3',
          nativeTheme: true,
          compactChrome: true
        });
      }
      await captureState(
        page,
        screen,
        `base-${phase}`,
        scheme,
        undefined,
        true
      );
    });
  }
}
