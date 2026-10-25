import { QueryClient } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { invalidateTestAlarmViews } from './invalidateTestAlarmViews';

it('refreshes history and every condition projection after a test command', async () => {
  const client = new QueryClient();
  const invalidate = vi.spyOn(client, 'invalidateQueries').mockResolvedValue();
  await invalidateTestAlarmViews(client);
  expect(
    invalidate.mock.calls.map(([options]) => options?.queryKey?.[0])
  ).toEqual(
    expect.arrayContaining([
      'machine-health-series',
      'machine-health-signals',
      'machine-health-summary',
      'station-mimic',
      'equipment-positions',
      'kanban-board-cards'
    ])
  );
});
