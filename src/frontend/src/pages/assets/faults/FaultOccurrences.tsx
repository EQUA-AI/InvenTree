import { plural, t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Box,
  Button,
  Divider,
  Group,
  Pagination,
  Paper,
  SegmentedControl,
  SimpleGrid,
  Skeleton,
  Spoiler,
  Stack,
  Table,
  Text,
  Title,
  useMantineTheme
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import {
  IconChevronDown,
  IconChevronRight,
  IconClockExclamation
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { MonitoringContinuityNotice } from '../health/MonitoringContinuity';
import { ageLabel } from '../health/mimic/FreshnessLabel';
import {
  type DisplayClock,
  DisplayClockContext,
  formatInstant,
  useDisplayClock
} from '../health/mimic/observedTime';
import operations from '../operations.module.css';

import { type ReactNode, useEffect, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../../contexts/ApiContext';
import {
  AlarmStateBadge,
  StatusBadge as Badge,
  SeverityBadge,
  qualityLabel
} from '../health/common';
import {
  detectorLabel,
  metricLabel,
  outsideOccurrenceSpan,
  retainedValue,
  shiftSpan,
  snapshotReasonLabel,
  workRelationLabel
} from './occurrenceLabels';
import {
  type FaultOccurrence,
  OCCURRENCE_PAGE_SIZE,
  type OccurrenceListState,
  type OccurrencePage,
  type OccurrenceStatusFilter,
  type RetainedSnapshot,
  occurrenceListParams,
  occurrenceListState,
  occurrenceRequest
} from './occurrenceModel';

function when(value: string | null | undefined, clock: DisplayClock): string {
  return formatInstant(value, clock) ?? t`Time unavailable`;
}

function lifecycleLabel(status: string): string {
  switch (status) {
    case 'completed':
      return t`Completed`;
    case 'canceled':
      return t`Canceled`;
    case 'in_progress':
      return t`In progress`;
    case 'on_hold':
      return t`On hold`;
    case 'planned':
      return t`Planned`;
    case 'ready':
      return t`Ready`;
    case 'verifying':
      return t`Verifying`;
    case 'draft':
      return t`Draft`;
    default:
      return status;
  }
}

function Field({
  label,
  children
}: Readonly<{ label: string; children: ReactNode }>) {
  return (
    <Stack gap={2}>
      <Text size='xs' className={operations.meta} fw={600}>
        {label}
      </Text>
      <div style={{ overflowWrap: 'anywhere' }}>{children}</div>
    </Stack>
  );
}

/** The source's own identity for the event, as it was recorded. */
function EventIdentity({
  occurrence
}: Readonly<{ occurrence: FaultOccurrence }>) {
  const ids = [
    occurrence.alarm_code && t`Code ${occurrence.alarm_code}`,
    occurrence.external_id && t`Source alarm ${occurrence.external_id}`
  ].filter(Boolean);
  return (
    <Text size='xs' className={operations.meta}>
      {ids.length ? ids.join(' · ') : t`No source alarm id`}
      {' · '}
      {t`Record ${occurrence.id}`}
    </Text>
  );
}

function Components({ occurrence }: Readonly<{ occurrence: FaultOccurrence }>) {
  if (!occurrence.components.length) {
    return (
      <Text size='sm' className={operations.meta}>
        {occurrence.signals.length
          ? t`Signal not mapped to a component`
          : t`No signal recorded`}
      </Text>
    );
  }
  return (
    <Stack gap={2}>
      {occurrence.components.map((component) => (
        <Text key={component.code} size='sm'>
          {component.name}
          {component.status === 'draft' && (
            <Text span size='xs' className={operations.meta}>
              {' · '}
              {t`unverified assignment`}
            </Text>
          )}
        </Text>
      ))}
    </Stack>
  );
}

function Evidence({ occurrence }: Readonly<{ occurrence: FaultOccurrence }>) {
  return (
    <Stack gap={2}>
      <Text size='sm'>{occurrence.source?.name ?? t`Source not recorded`}</Text>
      <Text size='xs' className={operations.meta}>
        {occurrence.snapshot_count
          ? plural(occurrence.snapshot_count, {
              one: '# retained snapshot',
              other: '# retained snapshots'
            })
          : t`No retained snapshot`}
      </Text>
    </Stack>
  );
}

function WorkLinks({
  occurrence,
  compact = false
}: Readonly<{ occurrence: FaultOccurrence; compact?: boolean }>) {
  if (!occurrence.work.length && !occurrence.repair_packet) {
    return (
      <Text size='sm' className={operations.meta}>
        {t`No owning work you can open`}
      </Text>
    );
  }
  return (
    <Stack gap={4}>
      {occurrence.work.map((work) => (
        <Stack key={work.id} gap={0}>
          <Anchor
            className={operations.link}
            component={Link}
            to={`/maintenance/work-orders/${work.id}/`}
            size='sm'
            fw={600}
          >
            {work.reference ?? t`Work order ${work.id}`}
          </Anchor>
          <Text size='xs' className={operations.meta}>
            {workRelationLabel(work.relation)} ·{' '}
            {lifecycleLabel(work.lifecycle_status)}
            {!compact && work.title ? ` · ${work.title}` : ''}
          </Text>
        </Stack>
      ))}
      {occurrence.repair_packet && (
        <Anchor
          className={operations.link}
          component={Link}
          to={`/repair/packets/${occurrence.repair_packet.id}/`}
          size='xs'
        >
          {t`Repair packet ${occurrence.repair_packet.reference}`}
        </Anchor>
      )}
    </Stack>
  );
}

function Outcome({
  occurrence,
  showNote = true
}: Readonly<{ occurrence: FaultOccurrence; showNote?: boolean }>) {
  return (
    <Stack gap={4} miw={0}>
      <AlarmStateBadge
        status={occurrence.status}
        severity={occurrence.severity}
      />
      {showNote && occurrence.resolution_note && (
        <Spoiler
          classNames={{ control: operations.link }}
          maxHeight={60}
          showLabel={t`Show full note`}
          hideLabel={t`Show less`}
        >
          <Text size='xs' className={operations.meta}>
            {occurrence.resolution_note}
          </Text>
        </Spoiler>
      )}
    </Stack>
  );
}

function RetainedSnapshotCard({
  snapshot,
  machineId,
  occurrence
}: Readonly<{
  snapshot: RetainedSnapshot;
  machineId: number;
  occurrence: FaultOccurrence;
}>) {
  const clock = useDisplayClock();
  const samples = snapshot.samples ?? [];
  const sameInstant = snapshot.window_start === snapshot.window_end;
  return (
    <Paper withBorder p='sm' data-snapshot={snapshot.id}>
      <Stack gap={6}>
        <Group justify='space-between' gap='xs' align='flex-start'>
          <Stack gap={0} miw={0}>
            <Text size='sm' fw={600} style={{ overflowWrap: 'anywhere' }}>
              {snapshot.signal_label || t`Signal not recorded`}
            </Text>
            <Text size='xs' className={operations.meta}>
              {snapshotReasonLabel(snapshot.reason)}
            </Text>
          </Stack>
          <Badge variant='outline' color='gray'>
            {t`Retained snapshot`}
          </Badge>
        </Group>
        {outsideOccurrenceSpan(snapshot, occurrence) && (
          <Badge
            color='gray'
            variant='outline'
            leftSection={<IconClockExclamation size={14} />}
          >{t`Outside occurrence span`}</Badge>
        )}
        <Text className={operations.meta}>
          {t`Sample age at capture`}:{' '}
          {ageLabel(
            (Date.parse(snapshot.captured_at) -
              Date.parse(snapshot.window_end ?? '')) /
              1000
          )}
        </Text>
        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing='xs'>
          <Field label={t`Sample window`}>
            <Text size='sm'>
              {sameInstant
                ? `${t`Single sample at`} ${when(snapshot.window_start, clock)}`
                : `${when(snapshot.window_start, clock)} — ${when(snapshot.window_end, clock)}`}
            </Text>
          </Field>
          <Field label={t`Captured`}>
            <Text size='sm'>{when(snapshot.captured_at, clock)}</Text>
          </Field>
          <Field label={t`Quality when captured`}>
            <Text size='sm'>
              {qualityLabel(snapshot.quality)}
              {snapshot.stale ? ` · ${t`already stale`}` : ''}
            </Text>
          </Field>
        </SimpleGrid>
        {samples.length ? (
          <Table.ScrollContainer minWidth={220} maxHeight={220}>
            <Table withRowBorders={false} verticalSpacing={2}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t`Observed`}</Table.Th>
                  <Table.Th>{t`Retained value`}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {samples.map((sample, index) => (
                  <Table.Tr key={`${sample.observed_at}-${index}`}>
                    <Table.Td>
                      <Text size='xs'>{when(sample.observed_at, clock)}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size='xs'>
                        {retainedValue(sample.value, snapshot.unit)}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ) : (
          <Text size='xs' className={operations.meta}>
            {t`No reading was available when this snapshot was captured; that absence is what was retained.`}
          </Text>
        )}
        <Text
          size='xs'
          className={operations.meta}
          style={{ overflowWrap: 'anywhere' }}
        >
          {snapshot.source_name ?? t`Source not recorded`} · {t`content hash`}{' '}
          {snapshot.content_hash.slice(0, 12)}
        </Text>
        <Anchor
          className={operations.link}
          component={Link}
          size='sm'
          to={`/machines/machine/${machineId}/health/?snapshot=${encodeURIComponent(snapshot.id)}`}
        >
          {t`Open exact retained snapshot`}
        </Anchor>
        <Text size='xs' className={operations.meta}>
          {snapshot.captured_by_name
            ? t`Captured by ${snapshot.captured_by_name}`
            : snapshot.system_actor
              ? t`Captured by system: ${snapshot.system_actor}`
              : t`Captured by: Not recorded`}
        </Text>
        {snapshot.captured_by_name && snapshot.system_actor && (
          <Text
            size='xs'
            className={operations.meta}
          >{t`System actor: ${snapshot.system_actor}`}</Text>
        )}
      </Stack>
    </Paper>
  );
}

/**
 * One occurrence's preserved account: what was observed, the evidence kept at
 * the time and the work that owns it. Read on demand; nothing is captured or
 * changed by opening it.
 */
function OccurrenceEvidence({
  machineId,
  occurrenceId,
  extra
}: Readonly<{
  machineId: number;
  occurrenceId: number;
  extra?: (detail: FaultOccurrence) => ReactNode;
}>) {
  const api = useApi();
  const detail = useQuery<FaultOccurrence>({
    queryKey: ['machine-fault-occurrence', machineId, occurrenceId],
    retry: false,
    queryFn: async ({ signal }) =>
      (
        await api.get(
          apiUrl(ApiEndpoints.asset_machine_fault_occurrence, machineId, {
            occurrence: occurrenceId
          }),
          { signal }
        )
      ).data
  });
  if (detail.isPending)
    return <Skeleton height={96} aria-label={t`Loading occurrence evidence`} />;
  if (detail.isError) {
    const denied =
      isAxiosError(detail.error) &&
      [401, 403, 404].includes(detail.error.response?.status ?? 0);
    return (
      <Alert
        color={denied ? 'gray' : 'red'}
        title={t`Retained evidence could not be loaded`}
      >
        <Stack gap='xs'>
          <Text size='sm'>
            {denied
              ? t`This occurrence is not available to you.`
              : t`Nothing is shown in its place. Try again.`}
          </Text>
          {!denied && (
            <Button
              size='xs'
              variant='light'
              w='fit-content'
              onClick={() => void detail.refetch()}
            >{t`Retry evidence`}</Button>
          )}
        </Stack>
      </Alert>
    );
  }
  return (
    <OccurrenceAccount
      machineId={machineId}
      occurrence={detail.data}
      extra={extra}
    />
  );
}

function OccurrenceAccount({
  machineId,
  occurrence,
  extra
}: Readonly<{
  machineId: number;
  occurrence: FaultOccurrence;
  extra?: (detail: FaultOccurrence) => ReactNode;
}>) {
  const clock = useDisplayClock();
  const metrics = Object.entries(occurrence.metrics ?? {})
    .map(([key, value]) => metricLabel(key, value, occurrence.metrics ?? {}))
    .filter(Boolean);
  const dismissed = occurrence.metrics?.dismissed as
    | { at?: string; by?: string; note?: string }
    | undefined;
  return (
    <Stack gap={6} data-testid='occurrence-evidence'>
      <Group gap={6} align='flex-start'>
        <Title order={5}>
          {occurrence.title ||
            occurrence.signals[0]?.display_name ||
            t`Occurrence ${occurrence.id}`}
        </Title>
        <Group gap='xs'>
          <SeverityBadge severity={occurrence.severity} />
          <Outcome occurrence={occurrence} showNote={false} />
        </Group>
      </Group>
      <Text className={operations.meta}>
        {t`Occurrence ${occurrence.id}`} ·{' '}
        {occurrence.alarm_code || t`Code not recorded`} ·{' '}
        {occurrence.components.map((c) => c.name).join(', ') ||
          t`Component not recorded`}
      </Text>
      {occurrence.status === 'resolved' && (
        <Text size='sm'>
          {t`Resolved`}{' '}
          {occurrence.resolved_at
            ? when(occurrence.resolved_at, clock)
            : t`time not recorded`}
        </Text>
      )}
      {dismissed && (
        <Text size='sm'>
          {t`Dismissed`} {when(dismissed.at, clock)} {t`by`}{' '}
          {dismissed.by ?? t`recorder not recorded`}:{' '}
          {dismissed.note ?? t`note not recorded`}
        </Text>
      )}
      {occurrence.resolution_note && (
        <Spoiler
          classNames={{ control: operations.link }}
          maxHeight={60}
          showLabel={t`Show full note`}
          hideLabel={t`Show less`}
        >
          <Text size='sm'>{occurrence.resolution_note}</Text>
        </Spoiler>
      )}
      <Title order={6}>{t`Original observation`}</Title>
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing='sm'>
        <Field label={t`First observed`}>
          <Text size='xs'>{when(occurrence.first_observed_at, clock)}</Text>
        </Field>
        <Field label={t`Last observed`}>
          <Text size='sm'>{when(occurrence.last_observed_at, clock)}</Text>
        </Field>
        <Field label={t`Raised by`}>
          <Text size='sm'>
            {detectorLabel(occurrence.detector)}
            {occurrence.detector_version
              ? ` · ${occurrence.detector_version}`
              : ''}
          </Text>
        </Field>
        <Field label={t`Acknowledgement`}>
          <Text size='sm'>
            {occurrence.acknowledged_at || occurrence.status === 'acknowledged'
              ? `${t`Acknowledged`} ${occurrence.acknowledged_at ? when(occurrence.acknowledged_at, clock) : t`time not recorded`} ${occurrence.acknowledged_by_name ? t`by ${occurrence.acknowledged_by_name}` : t`(recorder not recorded)`}`
              : t`Not acknowledged`}
          </Text>
        </Field>
      </SimpleGrid>
      <Text size='sm'>
        {occurrence.evidence_summary || t`No finding recorded`}
      </Text>
      {metrics.length > 0 && (
        <Box component='dl' m={0} className={operations.meta}>
          <Text component='dt' size='sm' fw={600}>{t`Detection metrics`}</Text>
          {metrics.map((label) => (
            <Text component='dd' key={label} m={0} size='sm'>
              {label}
            </Text>
          ))}
        </Box>
      )}
      {occurrence.signals.length > 0 && (
        <Text size='xs' className={operations.meta}>
          {t`Signals`}:{' '}
          {occurrence.signals
            .map((signal) =>
              signal.unit
                ? `${signal.display_name} (${signal.unit})`
                : signal.display_name
            )
            .join(', ')}
        </Text>
      )}
      {occurrence.display_shifted && (
        <Text
          className={operations.meta}
        >{t`Shown ${shiftSpan(occurrence.display_shift_seconds) ?? t`an unspecified interval`} later than recorded`}</Text>
      )}
      <Divider />
      <Group justify='space-between' gap='xs'>
        <Title order={6}>{t`Evidence retained at the time`}</Title>
        <Anchor
          size='sm'
          className={operations.link}
          component={Link}
          to={`/machines/machine/${machineId}/health/`}
        >
          {t`Current readings are on the Health tab`}
        </Anchor>
      </Group>
      {occurrence.snapshots.length ? (
        <Stack gap='xs'>
          {occurrence.snapshot_count > occurrence.snapshots.length && (
            <Text size='xs' className={operations.meta}>
              {t`Showing the ${occurrence.snapshots.length} most recent of ${occurrence.snapshot_count} retained snapshots.`}
            </Text>
          )}
          {occurrence.snapshots.map((snapshot) => (
            <RetainedSnapshotCard
              key={snapshot.id}
              snapshot={snapshot}
              occurrence={occurrence}
              machineId={machineId}
            />
          ))}
        </Stack>
      ) : (
        <Text size='sm' className={operations.meta}>
          {t`No evidence snapshot was retained for this occurrence. The record above is what was kept; today's readings are not put in its place.`}
        </Text>
      )}
      {extra?.(occurrence)}
      <Divider />
      <Title order={6}>{t`Work`}</Title>
      <WorkLinks occurrence={occurrence} />
    </Stack>
  );
}

/**
 * R6 UI08 occurrences: each alarm or detected condition recorded for the
 * machine, newest first, with the evidence retained at the time and the
 * work that owns the response.
 */
export function FaultOccurrences({
  machineId,
  renderInvestigation
}: Readonly<{
  machineId: number;
  /** Extra evidence for an opened occurrence (P40 investigation windows). */
  renderInvestigation?: (occurrence: FaultOccurrence) => ReactNode;
}>) {
  const api = useApi();
  const theme = useMantineTheme();
  const phone = useMediaQuery('(max-width: 48em)');
  const [params, setParams] = useSearchParams();
  const state = occurrenceListState(params);
  const request = occurrenceRequest(state);
  const query = useQuery<OccurrencePage>({
    queryKey: ['machine-fault-occurrences', machineId, request],
    retry: false,
    queryFn: async ({ signal }) =>
      (
        await api.get(
          apiUrl(ApiEndpoints.asset_machine_fault_occurrences, machineId),
          { signal, params: request }
        )
      ).data
  });
  // A failed refresh retains React Query data; it cannot support current list claims.
  const list = query.isSuccess ? query.data : undefined;
  const clock = useDisplayClock(list?.plant_timezone);
  const triggers = useRef(new Map<number, HTMLButtonElement | null>());
  const openedEvidence = useRef<HTMLElement>(null);
  useEffect(() => {
    if (state.open !== null)
      openedEvidence.current?.focus({ preventScroll: true });
  }, [state.open, query.data]);
  const lastOpened = useRef<number | null>(null);
  const update = (changes: Partial<OccurrenceListState>, replace = false) =>
    setParams(
      (previous) => occurrenceListParams(previous, { ...state, ...changes }),
      { replace }
    );
  // Closing an occurrence returns focus to the control that opened it.
  useEffect(() => {
    if (state.open === null && lastOpened.current !== null) {
      triggers.current.get(lastOpened.current)?.focus();
      lastOpened.current = null;
    }
  }, [state.open]);
  const toggle = (id: number) => {
    if (state.open === id) {
      lastOpened.current = id;
      update({ open: null }, true);
    } else update({ open: id }, true);
  };
  const lastPage = Math.max(
    1,
    Math.ceil((list?.count ?? 0) / OCCURRENCE_PAGE_SIZE)
  );
  const outOfRange = !!list && state.page > lastPage;
  useEffect(() => {
    if (query.isSuccess && !query.isFetching && outOfRange)
      update({ page: lastPage }, true);
  }, [query.isSuccess, query.isFetching, outOfRange, lastPage]);
  const denied =
    isAxiosError(query.error) &&
    [401, 403, 404].includes(query.error.response?.status ?? 0);

  const toggleButton = (occurrence: FaultOccurrence) => (
    <Button
      ref={(node) => {
        triggers.current.set(occurrence.id, node);
      }}
      size='compact-xs'
      className={operations.phoneTarget}
      variant='subtle'
      w='fit-content'
      aria-expanded={state.open === occurrence.id}
      aria-controls={`occurrence-${occurrence.id}`}
      leftSection={
        state.open === occurrence.id ? (
          <IconChevronDown size={14} />
        ) : (
          <IconChevronRight size={14} />
        )
      }
      onClick={() => toggle(occurrence.id)}
    >
      {state.open === occurrence.id ? t`Hide evidence` : t`Evidence`}
    </Button>
  );

  const expanded =
    state.open !== null ? (
      <Paper
        component='section'
        withBorder
        p='sm'
        id={`occurrence-${state.open}`}
        ref={openedEvidence}
        tabIndex={-1}
        aria-label={t`Opened occurrence evidence`}
      >
        <Stack gap='sm'>
          <Group justify='space-between' gap='xs'>
            <Button
              size='compact-xs'
              variant='subtle'
              className={`${operations.link} ${operations.phoneTarget}`}
              onClick={() => toggle(state.open!)}
            >{t`Close evidence`}</Button>
          </Group>
          <OccurrenceEvidence
            machineId={machineId}
            occurrenceId={state.open}
            extra={renderInvestigation}
          />
        </Stack>
      </Paper>
    ) : null;
  return (
    <DisplayClockContext value={clock}>
      <Stack
        gap='sm'
        className={operations.phoneOccurrences}
        data-testid='fault-occurrences'
        data-operational-panel='fault-history'
      >
        <Paper
          component='section'
          aria-labelledby='fault-history-title'
          className={operations.band}
        >
          <div className={operations.heading}>
            <Group gap={4}>
              <Title
                order={4}
                id='fault-history-title'
              >{t`Alarm and fault investigation`}</Title>
              <Badge
                color='gray'
                variant='outline'
              >{t`Recorded occurrences`}</Badge>
            </Group>
            <Button
              size='xs'
              variant='default'
              loading={query.isFetching}
              onClick={() => query.refetch()}
            >{t`Refresh`}</Button>
          </div>
          <Text className={operations.meta}>
            {t`Records read`}{' '}
            {formatInstant(query.dataUpdatedAt || null, clock) ??
              t`Time unavailable`}{' '}
            · {t`Times`}: {clock.label}
          </Text>
          <Text
            className={operations.caveat}
          >{t`Resolved does not always mean cleared. Acknowledged conditions remain active.`}</Text>
          <Text
            className={operations.caveat}
          >{t`Retained evidence is not current readings. Read the resolution note.`}</Text>
          <Box maw='70ch'>
            <MonitoringContinuityNotice compact />
          </Box>
        </Paper>
        <Group justify='space-between' align='flex-end' gap='sm'>
          <Stack gap={2}>
            <Title order={5}>{t`Occurrences`}</Title>
          </Stack>
          <SegmentedControl
            size='xs'
            color={`${theme.primaryColor}.7`}
            aria-label={t`Occurrence status`}
            value={state.status}
            onChange={(value) =>
              update({
                status: value as OccurrenceStatusFilter,
                page: 1,
                open: null
              })
            }
            data={[
              { value: 'all', label: t`All` },
              { value: 'active', label: t`Active` },
              { value: 'resolved', label: t`Resolved` }
            ]}
          />
        </Group>
        {query.isPending && (
          <Stack gap='xs' aria-label={t`Loading occurrences`}>
            {[1, 2, 3].map((row) => (
              <Skeleton key={row} height={56} />
            ))}
          </Stack>
        )}
        {query.isError && (
          <Alert
            color='red'
            title={
              denied
                ? t`Occurrences unavailable`
                : t`Occurrences could not be loaded`
            }
          >
            <Group justify='space-between' gap='xs'>
              <Text size='sm'>
                {denied
                  ? t`Your access to this machine's history could not be confirmed.`
                  : t`The history source is unavailable. Nothing is shown in its place.`}
              </Text>
              <Button
                size='xs'
                variant='light'
                onClick={() => void query.refetch()}
              >{t`Retry occurrences`}</Button>
            </Group>
          </Alert>
        )}
        {list && (
          <Text size='sm' className={operations.meta} component='output'>
            {plural(list.count, {
              one: '# occurrence',
              other: '# occurrences'
            })}
            {state.status === 'active'
              ? ` · ${t`active now`}`
              : state.status === 'resolved'
                ? ` · ${t`resolved`}`
                : ''}
            {' · '}
            {t`newest first`}
          </Text>
        )}
        {list && list.count === 0 && (
          <Paper withBorder p='md'>
            <Text size='sm' className={operations.meta}>
              {state.status === 'all'
                ? t`No alarm or detected condition has been recorded for this machine.`
                : state.status === 'active'
                  ? t`No occurrence is active on this machine now.`
                  : t`No resolved occurrence is recorded for this machine.`}
            </Text>
          </Paper>
        )}
        {list &&
          list.results.length > 0 &&
          (phone ? (
            <Stack gap='xs'>
              {list.results.map((occurrence) => (
                <Paper
                  key={occurrence.id}
                  withBorder
                  p='sm'
                  data-occurrence={occurrence.id}
                >
                  <Stack gap='xs'>
                    <Group justify='space-between' gap='xs' align='flex-start'>
                      <div style={{ flexShrink: 0 }}>
                        <SeverityBadge severity={occurrence.severity} />
                      </div>
                      <Outcome occurrence={occurrence} />
                    </Group>
                    <Text
                      size='sm'
                      fw={600}
                      style={{ overflowWrap: 'anywhere' }}
                    >
                      {occurrence.title}
                    </Text>
                    <EventIdentity occurrence={occurrence} />
                    <SimpleGrid cols={2} spacing='xs'>
                      <Field label={t`First observed`}>
                        <Text size='sm'>
                          {when(occurrence.first_observed_at, clock)}
                        </Text>
                      </Field>
                      {occurrence.last_observed_at !==
                        occurrence.first_observed_at && (
                        <Field label={t`Last observed`}>
                          <Text size='sm'>
                            {when(occurrence.last_observed_at, clock)}
                          </Text>
                        </Field>
                      )}
                      <Field label={t`Component`}>
                        <Components occurrence={occurrence} />
                      </Field>
                      <Field label={t`Source / evidence`}>
                        <Evidence occurrence={occurrence} />
                      </Field>
                      <Field label={t`Owning work`}>
                        <WorkLinks occurrence={occurrence} compact />
                      </Field>
                    </SimpleGrid>
                    {toggleButton(occurrence)}
                    {state.open === occurrence.id && expanded}
                  </Stack>
                </Paper>
              ))}
            </Stack>
          ) : (
            <Table.ScrollContainer minWidth={960}>
              <Table
                data-testid='fault-occurrence-table'
                verticalSpacing={4}
                style={{ tableLayout: 'fixed' }}
              >
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th w={95}>{t`Severity`}</Table.Th>
                    <Table.Th w={200}>{t`State`}</Table.Th>
                    <Table.Th w={165}>
                      {t`Observed`}
                      <Text
                        size='xs'
                        className={operations.meta}
                      >{t`Observed span, not event duration`}</Text>
                    </Table.Th>
                    <Table.Th w='26%'>{t`Event / finding`}</Table.Th>
                    <Table.Th>{t`Component`}</Table.Th>
                    <Table.Th>{t`Evidence`}</Table.Th>
                    <Table.Th>{t`Owning work`}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {list.results.map((occurrence) => (
                    <OccurrenceRows
                      key={occurrence.id}
                      occurrence={occurrence}
                      toggle={toggleButton(occurrence)}
                      expanded={state.open === occurrence.id ? expanded : null}
                    />
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          ))}
        {state.open !== null &&
          list &&
          !list.results.some((row) => row.id === state.open) &&
          expanded}
        {lastPage > 1 && (
          <Group justify='flex-end'>
            <nav aria-label={t`Occurrence pagination`}>
              <Pagination
                value={state.page}
                total={lastPage}
                onChange={(page) => update({ page, open: null })}
              />
            </nav>
          </Group>
        )}
      </Stack>
    </DisplayClockContext>
  );
}

function OccurrenceRows({
  occurrence,
  toggle,
  expanded
}: Readonly<{
  occurrence: FaultOccurrence;
  toggle: ReactNode;
  expanded?: ReactNode;
}>) {
  const clock = useDisplayClock();
  return (
    <>
      <Table.Tr data-occurrence={occurrence.id}>
        <Table.Td className={operations.cell}>
          <SeverityBadge severity={occurrence.severity} />
        </Table.Td>
        <Table.Td className={operations.cell}>
          <Outcome occurrence={occurrence} />
        </Table.Td>
        <Table.Td className={operations.cell}>
          <Stack gap={2}>
            <Text size='xs'>{when(occurrence.first_observed_at, clock)}</Text>
            {occurrence.last_observed_at !== occurrence.first_observed_at && (
              <Text size='xs' className={operations.meta}>
                {t`until`} {when(occurrence.last_observed_at, clock)}
              </Text>
            )}
            {toggle}
          </Stack>
        </Table.Td>
        <Table.Td className={operations.cell}>
          <Stack gap={4}>
            <Text size='sm' fw={600}>
              {occurrence.title}
            </Text>
            <EventIdentity occurrence={occurrence} />
          </Stack>
        </Table.Td>
        <Table.Td className={operations.cell}>
          <Components occurrence={occurrence} />
        </Table.Td>
        <Table.Td className={operations.cell}>
          <Evidence occurrence={occurrence} />
        </Table.Td>
        <Table.Td className={operations.cell}>
          <WorkLinks occurrence={occurrence} compact />
        </Table.Td>
      </Table.Tr>
      {expanded && (
        <Table.Tr>
          <Table.Td colSpan={7}>{expanded}</Table.Td>
        </Table.Tr>
      )}
    </>
  );
}
