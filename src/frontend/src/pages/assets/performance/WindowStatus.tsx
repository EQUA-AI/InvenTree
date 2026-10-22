import { t } from '@lingui/core/macro';
import { Group, Loader, Text } from '@mantine/core';
import dayjs from 'dayjs';
import { displayClockLabel } from '../displayClock';
import { StatusBadge } from '../health/StatusBadge';

import type { SeriesResponse } from '@lib/types/MachineHealth';

/** The viewer's clock, named: "Europe/London (UTC+01:00)". */
export const clockLabel = displayClockLabel;

/**
 * Edges of a window as a reader says them: the date only where it is not
 * today or the span crosses midnight, seconds only for a short window.
 */
export function formatWindow(start: number, end: number, now: number): string {
  const seconds = Math.max(1, Math.round((end - start) / 1000));
  const time = seconds <= 30 * 60 ? 'HH:mm:ss' : 'HH:mm';
  const sameDay = dayjs(start).isSame(dayjs(end), 'day');
  const today = sameDay && dayjs(end).isSame(dayjs(now), 'day');
  const from = dayjs(start).format(today ? time : `D MMM ${time}`);
  const to = dayjs(end).format(sameDay ? time : `D MMM ${time}`);
  return `${from} – ${to}`;
}

/**
 * R6 A19.9 / A19.11: which window the charts are actually drawing, in whose
 * clock, and whether that is still the previous window while a new one is
 * read. The native page keeps the last window on screen during a refetch -
 * rightly, a blank chart reads as a fault - but nothing said so: the range
 * control already showed the new choice above the old lines.
 */
export function WindowStatus({
  response,
  updating,
  now,
  showClock = true,
  announce = true
}: Readonly<{
  response: SeriesResponse | undefined;
  /** The response on screen is the previous query's, kept while this one loads. */
  updating: boolean;
  now: number;
  showClock?: boolean;
  announce?: boolean;
}>) {
  // Keep the announcement container mounted during loading and failed reads.
  // The visible error explains a failed request; this region never invents a window.
  const announcement = announce ? (
    <Text component='output' size='xs' aria-live='polite' aria-atomic='true'>
      {updating ? t`Updating selected history window.` : ''}
    </Text>
  ) : null;
  if (!response) return announcement;
  const start = new Date(response.window_start).getTime();
  const end = new Date(response.window_end).getTime();
  const seconds = Math.max(1, Math.round((end - start) / 1000));
  return (
    <Group
      gap={6}
      wrap='wrap'
      data-window-status={updating ? 'updating' : 'current'}
    >
      {updating && (
        <StatusBadge
          tone='neutral'
          size='sm'
          variant='outline'
          color='blue'
          leftSection={<Loader size={10} color='blue' />}
        >
          {t`Updating`}
        </StatusBadge>
      )}
      {announcement}
    </Group>
  );
}

export default WindowStatus;
