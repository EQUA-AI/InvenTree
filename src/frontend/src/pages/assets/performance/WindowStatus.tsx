import { t } from '@lingui/core/macro';
import { Badge, Group, Loader, Text } from '@mantine/core';
import dayjs from 'dayjs';

import type { SeriesResponse } from '@lib/types/MachineHealth';

import { formatDuration } from './format';

/** The viewer's clock, named: "Europe/London (UTC+01:00)". */
export function clockLabel(at: number = Date.now()): string {
  let zone = '';
  try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone ?? '';
  } catch {
    zone = '';
  }
  const offset = `UTC${dayjs(at).format('Z')}`;
  return zone ? `${zone} (${offset})` : offset;
}

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
  now
}: Readonly<{
  response: SeriesResponse | undefined;
  /** The response on screen is the previous query's, kept while this one loads. */
  updating: boolean;
  now: number;
}>) {
  if (!response) return null;
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
        <Badge
          size='sm'
          variant='light'
          color='blue'
          leftSection={<Loader size={10} color='blue' />}
        >
          {t`Updating`}
        </Badge>
      )}
      <Text size='xs' c='dimmed' role={updating ? 'status' : undefined}>
        {updating
          ? t`Charts still show the previous window, ${formatWindow(start, end, now)} (${formatDuration(seconds)}), until the new one arrives.`
          : t`Window on screen: ${formatWindow(start, end, now)} (${formatDuration(seconds)}).`}{' '}
        {t`Times in ${clockLabel(end)}.`}
      </Text>
    </Group>
  );
}

export default WindowStatus;
