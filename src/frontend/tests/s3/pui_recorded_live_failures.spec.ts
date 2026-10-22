import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { NOW, openHealth, trendCard } from './healthFixtures';
import { openPerformance } from './performanceFixtures';
import { openStation } from './stationFixtures';

nativeBaseline();
for (const asLive of [true, false]) {
  for (const screen of [
    'Health',
    'pump Performance',
    'station Performance'
  ] as const) {
    test(`O27 linked-window ${screen} failures preserve refusal with presentation ${asLive ? 'on' : 'off'}`, async ({
      page
    }, info) => {
      for (const failure of ['clock', 'span'] as const) {
        const from = new Date(
          NOW - (failure === 'clock' ? 3600_000 : 31 * 86400_000)
        ).toISOString();
        const to = new Date(NOW).toISOString();
        const search = new URLSearchParams({
          history_from: from,
          history_to: to,
          history_clock: failure === 'clock' ? 'recorded' : 'display'
        });
        let reads: URL[];
        if (screen === 'Health') {
          const requests = await openHealth(page, {
            recordedTelemetryAsLive: asLive,
            entrySearch: `?${search}`,
            dataRange: { display_shift_seconds: failure === 'clock' ? null : 0 }
          });
          reads = requests.chart;
        } else if (screen === 'pump Performance') {
          reads = await openPerformance(page, {
            recordedTelemetryAsLive: asLive,
            displayShift: failure === 'clock' ? null : 0,
            harness: 's3',
            harnessParams: {
              entry: `/machines/machine/18/performance?${search}`
            }
          });
        } else {
          reads = await openStation(page, {
            recordedTelemetryAsLive: asLive,
            displayShift: failure === 'clock' ? null : 0,
            entry: `/machines/machine/17/performance?${search}`
          });
        }
        const title =
          failure === 'clock'
            ? asLive
              ? 'Time range unavailable'
              : 'Recorded clock unknown'
            : 'Invalid investigation window';
        const alert = (screen === 'Health' ? trendCard(page) : page)
          .getByRole('alert')
          .filter({ hasText: title });
        await expect(alert).toBeVisible();
        if (failure === 'clock') {
          await expect(alert).toContainText(
            asLive
              ? 'No source window is read until it is known'
              : 'recorded-to-display clock offset'
          );
        } else {
          await expect(alert).toContainText(
            asLive ? 'no longer than 30 days' : '24 h'
          );
        }
        if (asLive)
          await expect(alert).not.toContainText(/recorded|historical|replay/i);
        expect(reads).toHaveLength(0);
        await alert.screenshot({
          path: info.outputPath(
            `o27-${screen.replaceAll(' ', '-')}-${failure}-${asLive ? 'on' : 'off'}.png`
          ),
          animations: 'disabled'
        });
      }
    });
  }
}
