import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { MachineHealthSummary } from '@lib/types/MachineHealth';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Stack,
  Text
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import dayjs from 'dayjs';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { workPanelParams } from '../../tasks/workorders/workPanelState';
import {
  type OccurrencePage,
  occurrenceRequest
} from '../faults/occurrenceModel';
import { HealthStateBadge, qualityLabel } from './common';

/** Policy continuity, never a claim that a source is currently available. */
export function MonitoringContinuityNotice() {
  return (
    <Text size='sm'>{t`Configured proactive monitoring applies before, during and after maintenance and Closeout. Its current availability is separate from repair, test and service outcomes.`}</Text>
  );
}

const when = (value?: string | null) =>
  value
    ? `${dayjs(value).format('DD MMM YYYY, HH:mm:ss')} (${Intl.DateTimeFormat().resolvedOptions().timeZone})`
    : t`Time unavailable`;

const unavailableInScope = (error: unknown) =>
  isAxiosError(error) && [401, 403, 404].includes(error.response?.status ?? 0);

/** Reuses Health/P12 reads only on demand; no new monitoring or write authority. */
export function MonitoringContinuity({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const [opened, setOpened] = useState(false);
  const [requested, setRequested] = useState(false);
  const [params] = useSearchParams();
  return (
    <Card
      component='details'
      withBorder
      style={{ overflowWrap: 'anywhere' }}
      open={opened}
      onToggle={(event) => {
        setOpened(event.currentTarget.open);
        if (event.currentTarget.open) setRequested(true);
      }}
      data-testid='monitoring-continuity'
    >
      <summary
        style={{ cursor: 'pointer', fontWeight: 600 }}
      >{t`Monitoring, deviations and prior records`}</summary>
      <Stack gap='sm' mt='sm'>
        <MonitoringContinuityNotice />
        {requested &&
          (workOrder.machine ? (
            <CurrentMonitoring
              machineId={workOrder.machine}
              originalOccurrence={workOrder.source_alert?.id}
            />
          ) : (
            <Alert color='gray'>{t`Monitoring unavailable — no machine is linked to this Work. This is not a failed repair or a healthy assessment.`}</Alert>
          ))}
        <Text size='sm'>{t`Post-repair observation requirements are unavailable: no sourced criteria, responsible owner or duration is supplied. No watch period is inferred from completion.`}</Text>
        <Text
          size='sm'
          fw={600}
        >{t`Factual correction or renewed investigation`}</Text>
        <Text size='sm'>{t`Factual corrections use attributed amendments and retain the original accepted fields. Renewed physical investigation needs the existing authorized reopened or linked Work flow. An occurrence on the same machine does not establish a recurrence relationship.`}</Text>
        <Text size='sm'>{t`Recurrence linkage and reopen policy are unavailable here. Prior accepted fields and original evidence remain inspectable; a complete immutable accepted report is not supplied by this server.`}</Text>
        {workOrder.source_alert && (
          <Anchor
            component={Link}
            to={`/machines/machine/${workOrder.source_alert.machine_id}/fault-history/?occurrence=${workOrder.source_alert.id}`}
            size='sm'
          >{t`Original occurrence for this Work`}</Anchor>
        )}
        <Group>
          <Anchor
            component={Link}
            to={`?${workPanelParams(params, 'diagnose')}`}
            size='sm'
          >{t`Saved requests and evidence for this Work`}</Anchor>
          <Anchor
            component={Link}
            to={`?${workPanelParams(params, 'closeout')}`}
            size='sm'
          >{t`Account, original fields and amendments`}</Anchor>
          <Anchor
            component={Link}
            to={`?${workPanelParams(params, 'history')}`}
            size='sm'
          >{t`Attributed Work history`}</Anchor>
        </Group>
      </Stack>
    </Card>
  );
}

function CurrentMonitoring({
  machineId,
  originalOccurrence
}: Readonly<{ machineId: number; originalOccurrence?: number }>) {
  const api = useApi();
  const summary = useQuery({
    queryKey: ['machine-health-summary', machineId],
    retry: false,
    queryFn: async () =>
      (
        await api.get<MachineHealthSummary>(
          apiUrl(ApiEndpoints.machine_health_summary, machineId)
        )
      ).data
  });
  const request = occurrenceRequest({ status: 'active', page: 1, open: null });
  const occurrences = useQuery({
    queryKey: ['machine-fault-occurrences', machineId, request],
    retry: false,
    queryFn: async () => {
      const response = await api.get<OccurrencePage>(
        apiUrl(ApiEndpoints.asset_machine_fault_occurrences, machineId),
        { params: request }
      );
      if (response.data.rule !== 'fault_occurrences.v1')
        throw new Error('Unsupported occurrence projection');
      return response.data;
    }
  });
  const data = !summary.isError ? summary.data : undefined;
  return (
    <Stack gap='sm' data-testid='current-monitoring'>
      <Button
        variant='default'
        loading={summary.isFetching || occurrences.isFetching}
        onClick={() => {
          void Promise.all([summary.refetch(), occurrences.refetch()]);
        }}
      >
        {t`Refresh monitoring and deviations`}
      </Button>
      {summary.isPending ? (
        <Text size='sm'>{t`Reading current monitoring configuration…`}</Text>
      ) : !data ? (
        <Alert color='gray'>
          <Stack gap='xs'>
            <Text size='sm'>
              {unavailableInScope(summary.error)
                ? t`Monitoring unavailable in the current scope. This is not a failed repair or a healthy assessment.`
                : t`Monitoring could not be read. This is not a failed repair or a healthy assessment.`}
            </Text>
            <Button
              variant='default'
              onClick={() => void summary.refetch()}
            >{t`Retry monitoring read`}</Button>
          </Stack>
        </Alert>
      ) : (
        <>
          <Text size='sm' fw={600}>
            {data.configured
              ? t`Current configured monitoring`
              : t`No health source configured — condition unknown`}
          </Text>
          <Text size='sm'>
            {t`Latest observation`}: {when(data.last_observed_at)}
          </Text>
          {data.configured && (
            <Group gap='xs'>
              <Text size='sm'>{t`Current Health state`}</Text>
              <HealthStateBadge state={data.state} />
              <Text size='sm'>
                {data.degraded_data
                  ? t`Data degraded`
                  : t`See source freshness and quality`}
              </Text>
            </Group>
          )}
          {data.sources.map((source) => (
            <Card key={source.source_id} withBorder padding='sm'>
              <Group justify='space-between' align='flex-start'>
                <Text size='sm' fw={600} style={{ overflowWrap: 'anywhere' }}>
                  {source.name}
                </Text>
                <Badge
                  color='gray'
                  styles={{
                    label: { whiteSpace: 'normal' },
                    root: { height: 'auto' }
                  }}
                >
                  {!source.active
                    ? t`Disabled`
                    : source.healthy
                      ? t`Connected source`
                      : t`Source unavailable`}
                </Badge>
              </Group>
              <Text size='sm'>
                {t`Last source success`}: {when(source.last_success_at)}
              </Text>
              <Text size='sm'>
                {t`Last source error`}: {when(source.last_error_at)}
              </Text>
            </Card>
          ))}
        </>
      )}
      <Anchor
        component={Link}
        to={`/machines/machine/${machineId}/health/`}
        size='sm'
      >{t`Current signals, quality and configured limits`}</Anchor>
      <Text size='sm' fw={600}>{t`Recorded active deviations`}</Text>
      {occurrences.isPending ? (
        <Text size='sm'>{t`Reading recorded deviations…`}</Text>
      ) : occurrences.isError || !occurrences.data ? (
        <Alert color='gray'>
          <Stack gap='xs'>
            <Text size='sm'>
              {unavailableInScope(occurrences.error)
                ? t`Deviation records unavailable in the current scope. No zero count or repair outcome can be established.`
                : t`Deviation records could not be read. No zero count or repair outcome can be established.`}
            </Text>
            <Button
              variant='default'
              onClick={() => void occurrences.refetch()}
            >{t`Retry deviation read`}</Button>
          </Stack>
        </Alert>
      ) : (
        <>
          <Text size='sm'>{t`Showing ${occurrences.data.results.length} of ${occurrences.data.count} active occurrence records. This is not a recurrence count or a repair verdict.`}</Text>
          {occurrences.data.results.map((occurrence) => (
            <Card key={occurrence.id} withBorder padding='sm'>
              <Stack gap={4}>
                <Text size='sm' fw={600}>
                  {occurrence.title}
                </Text>
                <Text size='sm'>
                  {occurrence.id === originalOccurrence
                    ? t`Original occurrence for this Work`
                    : t`Relationship to prior Work unconfirmed`}{' '}
                  · {occurrence.status}
                </Text>
                <Text size='sm'>
                  {t`Source`}: {occurrence.source?.name || t`Not recorded`} ·{' '}
                  {occurrence.external_id || t`No source alarm id`}
                </Text>
                <Text size='sm'>
                  {t`Signals`}:{' '}
                  {occurrence.signals
                    .map((signal) => signal.display_name)
                    .join(', ') || t`Not recorded`}
                </Text>
                <Text size='sm'>
                  {t`First observed`}: {when(occurrence.first_observed_at)}
                </Text>
                <Text size='sm'>
                  {t`Last observed`}: {when(occurrence.last_observed_at)}
                </Text>
                <Text size='sm'>
                  {t`Retained snapshots`}: {occurrence.snapshot_count}
                </Text>
                {occurrence.snapshots.map((snapshot) => (
                  <Text size='sm' key={snapshot.id}>
                    {snapshot.signal_label} · {t`Quality at capture`}:{' '}
                    {qualityLabel(snapshot.quality)} ·{' '}
                    {snapshot.stale
                      ? t`Stale at capture`
                      : t`Not stale at capture`}{' '}
                    · {t`Captured`}: {when(snapshot.captured_at)}
                  </Text>
                ))}
                {occurrence.snapshot_count === 0 && (
                  <Text size='sm'>{t`Retained quality unavailable; current readings are not substituted.`}</Text>
                )}
                <Anchor
                  component={Link}
                  to={`/machines/machine/${machineId}/fault-history/?occurrence=${occurrence.id}`}
                  size='sm'
                >{t`Open exact occurrence and retained evidence`}</Anchor>
                {occurrence.work.map((work) => (
                  <Anchor
                    key={work.id}
                    component={Link}
                    to={`/maintenance/work-orders/${work.id}/?work_panel=diagnose`}
                    size='sm'
                  >
                    {work.reference || t`Work ${work.id}`} ·{' '}
                    {work.lifecycle_status.replaceAll('_', ' ')}
                  </Anchor>
                ))}
                <Text size='sm'>
                  {occurrence.work.length
                    ? t`Current owner, requests and checkpoints: open the owning Work. No owner is inferred from the prior completer.`
                    : t`No owning Work is available in scope. Response owner and checkpoint are unavailable.`}
                </Text>
              </Stack>
            </Card>
          ))}
        </>
      )}
      <Text
        size='xs'
        c='dimmed'
      >{t`Original occurrence and capture times are retained. Browser timezone conversion is labelled; no dashboard display shift is applied.`}</Text>
      <Anchor
        component={Link}
        to={`/machines/machine/${machineId}/fault-history/`}
        size='sm'
      >{t`All occurrences and retained investigation windows`}</Anchor>
    </Stack>
  );
}
