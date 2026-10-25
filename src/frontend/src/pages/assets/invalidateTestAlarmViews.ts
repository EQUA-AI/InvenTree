import type { QueryClient } from '@tanstack/react-query';

export async function invalidateTestAlarmViews(queryClient: QueryClient) {
  await Promise.all(
    [
      'machine-health-signals',
      'machine-health-series',
      'machine-health-summary',
      'machine-health-anomalies',
      'machine-metrics',
      'station-mimic',
      'equipment-positions',
      'dashboard-machine-alarms',
      'kanban-cards',
      'kanban-board-cards',
      'asset-machines',
      'test-alarm-targets'
    ].map((key) => queryClient.invalidateQueries({ queryKey: [key] }))
  );
}
