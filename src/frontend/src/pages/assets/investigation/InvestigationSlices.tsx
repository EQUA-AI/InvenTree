import { t } from '@lingui/core/macro';
import { Alert, Button, Group, SimpleGrid, Stack, Text } from '@mantine/core';
import { DateTimePicker } from '@mantine/dates';
import dayjs from 'dayjs';
import { useState } from 'react';
import { clockLabel } from '../performance/WindowStatus';
import {
  type InvestigationWindow,
  MAX_HISTORY_WINDOW_MS,
  pickerInstant,
  sliceAt,
  sliceCount,
  validSpan
} from './windows';

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
  const [from, setFrom] = useState<string | null>(
    dayjs(initial.from).format('YYYY-MM-DD HH:mm:ss')
  );
  const [to, setTo] = useState<string | null>(
    dayjs(initial.to).format('YYYY-MM-DD HH:mm:ss')
  );
  const [span, setSpan] = useState(initial);
  const [index, setIndex] = useState(0);
  const start = pickerInstant(from);
  const end = pickerInstant(to);
  const valid = validSpan(start, end);
  const limit = Math.min(maxWindowMs, MAX_HISTORY_WINDOW_MS);
  const count = sliceCount(span, limit);
  const slice = sliceAt(span, index, limit);
  return (
    <Stack gap='xs' data-testid='investigation-slices'>
      <Text fw={600} size='sm'>{t`Linked investigation windows`}</Text>
      <Text
        size='xs'
        c='dimmed'
      >{t`Choose the case span, then read one slice at a time (at most ${limit / 3600_000} h each). No background read of the entire span. Source availability is metadata, not proof of samples. Times in ${clockLabel()}.`}</Text>
      <SimpleGrid
        cols={{ base: 1, sm: 2, lg: 3 }}
        style={{ alignItems: 'end' }}
      >
        <DateTimePicker
          label={t`Case span from`}
          value={from}
          onChange={setFrom}
        />
        <DateTimePicker label={t`Case span to`} value={to} onChange={setTo} />
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
            {new Date(slice.from).toLocaleString()} —{' '}
            {new Date(slice.to).toLocaleString()}
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
              size='xs'
              onClick={() => onRead(slice)}
            >{t`Read this slice`}</Button>
          </Group>
        </>
      )}
    </Stack>
  );
}
