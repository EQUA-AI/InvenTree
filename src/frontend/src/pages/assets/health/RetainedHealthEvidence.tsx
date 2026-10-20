import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { HealthEvidenceSnapshot } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Paper,
  Stack,
  Table,
  Text,
  Title
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { snapshotReasonLabel } from '../faults/occurrenceLabels';
import { fullInstant } from './SignalEvidence';
import { qualityLabel } from './common';

/** Historical citations are read separately and never replace current state. */
export function RetainedHealthEvidence({
  machineId
}: Readonly<{ machineId: number }>) {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const snapshotId = params.get('snapshot');
  const [listed, setListed] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const list = useQuery<HealthEvidenceSnapshot[]>({
    queryKey: ['machine-health-snapshots', machineId],
    enabled: listed,
    staleTime: 60000,
    queryFn: async () =>
      (await api.get(apiUrl(ApiEndpoints.machine_health_snapshots, machineId)))
        .data.results
  });
  const exact = useQuery<HealthEvidenceSnapshot>({
    queryKey: ['machine-health-snapshot', machineId, snapshotId],
    enabled: snapshotId !== null,
    retry: false,
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_snapshots, machineId),
        { params: { snapshot: snapshotId } }
      );
      const snapshot = response.data.results?.find(
        (row: HealthEvidenceSnapshot) =>
          row.id === snapshotId && row.machine === machineId
      );
      if (!snapshot) throw new Error('Snapshot not returned');
      return snapshot;
    }
  });
  useEffect(() => {
    if (snapshotId) heading.current?.focus();
  }, [snapshotId]);
  const snapshot = exact.data;
  const select = (id: string | null) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (id) next.set('snapshot', id);
      else next.delete('snapshot');
      return next;
    });
    if (!id) requestAnimationFrame(() => trigger.current?.focus());
  };
  return (
    <Paper
      component='section'
      withBorder
      radius='md'
      p='md'
      aria-label={t`Retained evidence`}
    >
      <Stack gap='sm'>
        <Group justify='space-between'>
          <Text size='sm' fw={600}>
            {snapshotId
              ? t`Retained incident evidence`
              : t`Current observations`}
          </Text>
          <Button
            ref={trigger}
            variant='subtle'
            size='xs'
            onClick={() => setListed((value) => !value)}
            aria-expanded={listed}
          >
            {listed
              ? t`Hide retained evidence list`
              : t`Browse retained evidence`}
          </Button>
        </Group>
        <Text
          size='sm'
          c='dimmed'
        >{t`Current readings and condition remain below. Retained snapshots preserve the original fault-time values and clock; reconnecting a source does not replace them.`}</Text>
        {listed && (
          <Stack gap='xs'>
            {list.isLoading && <Loader size='xs' />}
            {list.isError && (
              <Text size='sm'>{t`Retained evidence could not be read; absence is not established.`}</Text>
            )}
            {list.isSuccess && (
              <Text
                size='xs'
                c='dimmed'
              >{t`Latest ${list.data.length} machine snapshots (maximum 50); older citations remain accessible by their exact links.`}</Text>
            )}
            {list.data?.map((row) => (
              <Button
                key={row.id}
                variant='light'
                color='violet'
                h='auto'
                py='xs'
                styles={{ label: { whiteSpace: 'normal', textAlign: 'left' } }}
                onClick={() => select(row.id)}
              >{t`Retained: ${row.signal_label} · ${fullInstant(row.captured_at) ?? '—'}`}</Button>
            ))}
          </Stack>
        )}
        {snapshotId && (
          <Stack gap='sm'>
            <Title
              order={5}
              ref={heading}
              tabIndex={-1}
            >{t`Retained snapshot`}</Title>
            <Button
              variant='default'
              onClick={() => select(null)}
            >{t`Return to current observations`}</Button>
            {exact.isLoading && <Loader size='sm' />}
            {exact.isError && (
              <Alert
                color='gray'
                title={t`Retained snapshot unavailable`}
              >{t`This exact citation could not be read in this machine's scope. Current data is not a substitute.`}</Alert>
            )}
            {snapshot && (
              <Stack gap='xs'>
                <Group>
                  <Badge color='violet'>{t`Retained; not current`}</Badge>
                  <Text fw={600}>{snapshot.signal_label}</Text>
                </Group>
                <Text size='sm'>{t`Captured ${fullInstant(snapshot.captured_at) ?? '—'} · window ${fullInstant(snapshot.window_start) ?? '—'} to ${fullInstant(snapshot.window_end) ?? '—'}`}</Text>
                <Text size='sm'>
                  {qualityLabel(snapshot.quality)}
                  {snapshot.stale ? ` · ${t`Stale when captured`}` : ''}
                </Text>
                <Text size='sm'>{t`Source: ${String(snapshot.source_references.source ?? t`Not recorded`)} · anomaly: ${snapshot.anomaly ?? t`Not recorded`}`}</Text>
                <Text size='sm'>{snapshotReasonLabel(snapshot.reason)}</Text>
                <Text size='sm'>
                  {snapshot.captured_by_name
                    ? t`Captured by ${snapshot.captured_by_name}`
                    : !snapshot.system_actor
                      ? t`Recorder not recorded`
                      : null}
                </Text>
                {snapshot.system_actor && (
                  <Text size='sm'>{t`System actor: ${snapshot.system_actor}`}</Text>
                )}
                <Text
                  size='xs'
                  style={{ overflowWrap: 'anywhere' }}
                >{t`Snapshot ${snapshot.id} · hash ${snapshot.content_hash}`}</Text>
                <Text
                  size='xs'
                  c='dimmed'
                >{t`Only captured observations are shown. A snapshot does not reconstruct unrecorded history.`}</Text>
                <Table.ScrollContainer minWidth={450}>
                  <Table aria-label={t`Retained observations`}>
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>{t`Source observation time`}</Table.Th>
                        <Table.Th>{t`Captured value`}</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {snapshot.samples.map((sample, index) => (
                        <Table.Tr key={`${sample.observed_at}-${index}`}>
                          <Table.Td>
                            {fullInstant(sample.observed_at) ?? t`Not recorded`}
                          </Table.Td>
                          <Table.Td>
                            {sample.value === null || sample.value === undefined
                              ? t`No observation`
                              : typeof sample.value === 'object'
                                ? JSON.stringify(sample.value)
                                : String(sample.value)}{' '}
                            {snapshot.unit}
                          </Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
                {snapshot.samples.length === 0 && (
                  <Text size='sm'>{t`No observations captured in this snapshot`}</Text>
                )}
              </Stack>
            )}
          </Stack>
        )}
      </Stack>
    </Paper>
  );
}
