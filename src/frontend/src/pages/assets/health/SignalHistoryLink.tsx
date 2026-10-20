import { t } from '@lingui/core/macro';
import { Anchor, Text } from '@mantine/core';
import { Link, useSearchParams } from 'react-router-dom';
import { boundedWindow, sliceAt } from '../investigation/windows';

/** Bounds come from their provider. Occurrences and snapshots link stable
 * recorded instants. Point observations never acquire an invented duration. */
export function SignalHistoryLink({
  machineId,
  bindingId,
  from,
  to,
  clock,
  occurrence,
  snapshot
}: Readonly<{
  machineId: number;
  bindingId: number;
  from: string | null;
  to: string | null;
  clock: 'recorded' | 'display';
  occurrence?: number | null;
  snapshot?: string;
}>) {
  const [current] = useSearchParams();
  const start = Date.parse(from ?? '');
  const end = Date.parse(to ?? '');
  const window =
    boundedWindow(start, end) ?? sliceAt({ from: start, to: end }, 0);
  if (!window)
    return (
      <Text
        size='xs'
        c='dimmed'
      >{t`No increasing source window recorded; no event duration is inferred.`}</Text>
    );
  const params = new URLSearchParams(current);
  params.set('history_from', new Date(window.from).toISOString());
  params.set('history_to', new Date(window.to).toISOString());
  params.set('history_clock', clock);
  params.set('binding', String(bindingId));
  if (occurrence) params.set('occurrence', String(occurrence));
  else params.delete('occurrence');
  if (snapshot) params.set('snapshot', snapshot);
  else params.delete('snapshot');
  return (
    <Anchor
      component={Link}
      size='sm'
      to={`/machines/machine/${machineId}/health/?${params}`}
    >
      {snapshot
        ? t`Investigate snapshot source window`
        : t`Investigate occurrence source window`}
      {window.to < end ? ` · ${t`first bounded 24 h span`}` : ''}
    </Anchor>
  );
}
