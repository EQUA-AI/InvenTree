import { t } from '@lingui/core/macro';
import { Alert, Button, Group, SimpleGrid, Stack, Text } from '@mantine/core';
import { DateTimePicker } from '@mantine/dates';
import dayjs from 'dayjs';
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';
import { useState } from 'react';
import { formatInstant, useDisplayClock } from '../health/mimic/observedTime';
import operations from '../operations.module.css';
import {
  type InvestigationWindow,
  MAX_HISTORY_WINDOW_MS,
  sliceAt,
  sliceCount,
  validSpan
} from './windows';

// Vite's dev CommonJS interop initializes dayjs at its import declaration.
// Keep plugin initialization after all imports so it cannot read it in the TDZ.
dayjs.extend(utc);
dayjs.extend(timezone);

/** A reusable selector only: advancing never prefetches a whole case span. */
export function InvestigationSlices({
  initial,
  onRead,
  maxWindowMs = MAX_HISTORY_WINDOW_MS
}: Readonly<{
  initial: InvestigationWindow;
  onRead: (window: InvestigationWindow) => void;
  /** Use the actual consuming endpoint limit (Health trends: 6 h). */
  maxWindowMs?: number;
}>) {
  const clock = useDisplayClock();
  const [from, setFrom] = useState<string | null>(
    dayjs(initial.from).tz(clock.zone).format('YYYY-MM-DD HH:mm:ss')
  );
  const [to, setTo] = useState<string | null>(
    dayjs(initial.to).tz(clock.zone).format('YYYY-MM-DD HH:mm:ss')
  );
  const [span, setSpan] = useState(initial);
  const [index, setIndex] = useState(0);
  const start = from ? dayjs.tz(from, clock.zone).valueOf() : Number.NaN;
  const end = to ? dayjs.tz(to, clock.zone).valueOf() : Number.NaN;
  const valid = validSpan(start, end);
  const limit = Math.min(maxWindowMs, MAX_HISTORY_WINDOW_MS);
  const count = sliceCount(span, limit);
  const slice = sliceAt(span, index, limit);
  return (
    <Stack gap='xs' data-testid='investigation-slices'>
      <Text fw={600} size='sm'>{t`Linked investigation windows`}</Text>
      <Text
        size='xs'
        className={operations.meta}
      >{t`Choose the case span, then read one slice at a time (at most ${limit / 3600_000} h each). No background read of the entire span. Source availability is metadata, not proof of samples.`}</Text>
      <SimpleGrid
        cols={{ base: 1, sm: 2, lg: 3 }}
        style={{ alignItems: 'end' }}
      >
        <DateTimePicker
          classNames={{ input: operations.input }}
          valueFormat='D MMM YYYY HH:mm'
          label={t`Case span from (${clock.zone})`}
          value={from}
          onChange={setFrom}
        />
        <DateTimePicker
          classNames={{ input: operations.input }}
          valueFormat='D MMM YYYY HH:mm'
          label={t`Case span to (${clock.zone})`}
          value={to}
          onChange={setTo}
        />
        <Button
          variant='light'
          disabled={!valid}
          onClick={() => {
            setSpan({ from: start, to: end });
            setIndex(0);
          }}
        >{t`Set case span`}</Button>
      </SimpleGrid>
      {!valid && (
        <Alert color='yellow'>{t`Choose a valid increasing case span.`}</Alert>
      )}
      {slice && (
        <>
          <Text size='xs'>
            {t`Slice ${index + 1} of ${count}`}:{' '}
            {formatInstant(slice.from, clock) ?? t`Time unavailable`} —{' '}
            {formatInstant(slice.to, clock) ?? t`Time unavailable`}
          </Text>
          <Group gap='xs'>
            <Button
              variant='default'
              size='xs'
              disabled={index === 0}
              onClick={() => setIndex((value) => value - 1)}
            >{t`Previous slice`}</Button>
            <Button
              variant='default'
              size='xs'
              disabled={index + 1 >= count}
              onClick={() => setIndex((value) => value + 1)}
            >{t`Next slice`}</Button>
            <Button
              color='var(--mantine-primary-color-7)'
              size='xs'
              onClick={() => onRead(slice)}
            >{t`Read this slice`}</Button>
          </Group>
        </>
      )}
    </Stack>
  );
}
