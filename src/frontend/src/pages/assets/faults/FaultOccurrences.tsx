import { plural, t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Divider,
  Group,
  Pagination,
  Paper,
  SegmentedControl,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  Title
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconChevronDown, IconChevronRight } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import dayjs from 'dayjs';
import { type ReactNode, useEffect, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../../contexts/ApiContext';
import { SeverityBadge, qualityLabel } from '../health/common';
import {
  detectorLabel,
  occurrenceStatusLabel,
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

function when(value: string | null | undefined): string {
  return value ? dayjs(value).format('D MMM YYYY HH:mm:ss') : '—';
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
      <Text size='xs' c='dimmed' fw={600}>
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
    <Text size='xs' c='dimmed'>
      {ids.length ? ids.join(' · ') : t`No source alarm id`}
      {' · '}
      {t`Record ${occurrence.id}`}
    </Text>
  );
}

function Components({ occurrence }: Readonly<{ occurrence: FaultOccurrence }>) {
  if (!occurrence.components.length) {
    return (
      <Text size='sm' c='dimmed'>
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
            <Text span size='xs' c='dimmed'>
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
      <Text size='xs' c='dimmed'>
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
      <Text size='sm' c='dimmed'>
        {t`No owning work recorded`}
      </Text>
    );
  }
  return (
    <Stack gap={4}>
      {occurrence.work.map((work) => (
        <Stack key={work.id} gap={0}>
          <Anchor
            component={Link}
            to={`/maintenance/work-orders/${work.id}/`}
            size='sm'
            fw={600}
          >
            {work.reference ?? t`Work order ${work.id}`}
          </Anchor>
          <Text size='xs' c='dimmed'>
            {workRelationLabel(work.relation)} ·{' '}
            {lifecycleLabel(work.lifecycle_status)}
            {!compact && work.title ? ` · ${work.title}` : ''}
          </Text>
        </Stack>
      ))}
      {occurrence.repair_packet && (
        <Anchor
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

function Outcome({ occurrence }: Readonly<{ occurrence: FaultOccurrence }>) {
  return (
    <Stack gap={2}>
      <Badge
        variant={occurrence.active ? 'light' : 'outline'}
        color={occurrence.active ? 'orange' : 'gray'}
        w='fit-content'
      >
        {occurrenceStatusLabel(occurrence.status)}
      </Badge>
      {occurrence.status === 'acknowledged' && (
        <Text size='xs' c='dimmed'>
          {t`Acknowledged, not resolved`}
        </Text>
      )}
      {occurrence.resolution_note && (
        <Text size='xs' c='dimmed' lineClamp={3}>
          {occurrence.resolution_note}
        </Text>
      )}
    </Stack>
  );
}

function ClockNote({
  shifted,
  seconds
}: Readonly<{ shifted: boolean; seconds: number }>) {
  const span = shifted ? shiftSpan(seconds) : null;
  if (!span) return null;
  return (
    <Text size='xs' c='dimmed'>
      {t`Observation times are on the dashboard clock, moved forward ${span} from the plant's own clock.`}
    </Text>
  );
}

function RetainedSnapshotCard({
  snapshot
}: Readonly<{ snapshot: RetainedSnapshot }>) {
  const samples = snapshot.samples ?? [];
  const sameInstant = snapshot.window_start === snapshot.window_end;
  return (
    <Paper withBorder p='sm' data-snapshot={snapshot.id}>
      <Stack gap={6}>
        <Group justify='space-between' gap='xs' align='flex-start'>
          <Stack gap={0} style={{ minWidth: 0 }}>
            <Text size='sm' fw={600} style={{ overflowWrap: 'anywhere' }}>
              {snapshot.signal_label || t`Signal not recorded`}
            </Text>
            <Text size='xs' c='dimmed'>
              {snapshotReasonLabel(snapshot.reason)}
            </Text>
          </Stack>
          <Badge variant='outline' color='gray'>
            {t`Retained snapshot`}
          </Badge>
        </Group>
        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing='xs'>
          <Field label={t`Retained window`}>
            <Text size='sm'>
              {sameInstant
                ? when(snapshot.window_start)
                : `${when(snapshot.window_start)} — ${when(snapshot.window_end)}`}
            </Text>
          </Field>
          <Field label={t`Captured`}>
            <Text size='sm'>{when(snapshot.captured_at)}</Text>
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
                      <Text size='xs'>{when(sample.observed_at)}</Text>
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
          <Text size='xs' c='dimmed'>
            {t`No reading was available when this snapshot was captured; that absence is what was retained.`}
          </Text>
        )}
        <Text size='xs' c='dimmed' style={{ overflowWrap: 'anywhere' }}>
          {snapshot.source_name ?? t`Source not recorded`} · {t`content hash`}{' '}
          {snapshot.content_hash.slice(0, 12)}
        </Text>
        <ClockNote
          shifted={snapshot.display_shifted}
          seconds={snapshot.display_shift_seconds}
        />
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
  occurrence,
  extra
}: Readonly<{
  machineId: number;
  occurrence: FaultOccurrence;
  extra?: (detail: FaultOccurrence) => ReactNode;
}>) {
  const api = useApi();
  const detail = useQuery<FaultOccurrence>({
    queryKey: ['machine-fault-occurrence', machineId, occurrence.id],
    retry: false,
    queryFn: async ({ signal }) =>
      (
        await api.get(
          apiUrl(ApiEndpoints.asset_machine_fault_occurrence, machineId, {
            occurrence: occurrence.id
          }),
          { signal }
        )
      ).data
  });
  const metrics = Object.entries(occurrence.metrics ?? {}).filter(
    ([, value]) =>
      value === null || ['number', 'string', 'boolean'].includes(typeof value)
  );
  return (
    <Stack gap='sm' data-testid='occurrence-evidence'>
      <Title order={6}>{t`Original observation`}</Title>
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing='sm'>
        <Field label={t`First observed`}>
          <Text size='sm'>{when(occurrence.first_observed_at)}</Text>
        </Field>
        <Field label={t`Last observed`}>
          <Text size='sm'>{when(occurrence.last_observed_at)}</Text>
        </Field>
        <Field label={t`Raised by`}>
          <Text size='sm'>
            {detectorLabel(occurrence.detector)}
            {occurrence.detector_version
              ? ` · ${occurrence.detector_version}`
              : ''}
          </Text>
        </Field>
        <Field label={t`Seen by`}>
          <Text size='sm'>
            {occurrence.acknowledged_by_name
              ? t`Acknowledged by ${occurrence.acknowledged_by_name}`
              : t`Not acknowledged`}
          </Text>
        </Field>
      </SimpleGrid>
      {occurrence.evidence_summary && (
        <Text size='sm'>{occurrence.evidence_summary}</Text>
      )}
      {metrics.length > 0 && (
        <Group gap='xs'>
          {metrics.map(([key, value]) => (
            <Badge key={key} variant='outline' color='gray' tt='none'>
              {key}: {String(value)}
            </Badge>
          ))}
        </Group>
      )}
      {occurrence.signals.length > 0 && (
        <Text size='xs' c='dimmed'>
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
      <ClockNote
        shifted={occurrence.display_shifted}
        seconds={occurrence.display_shift_seconds}
      />
      <Divider />
      <Group justify='space-between' gap='xs'>
        <Title order={6}>{t`Evidence retained at the time`}</Title>
        <Anchor component={Link} to={`/machines/machine/${machineId}/health/`}>
          {t`Current readings are on the Health tab`}
        </Anchor>
      </Group>
      {detail.isPending && <Skeleton height={96} />}
      {detail.isError && (
        <Alert color='red' title={t`Retained evidence could not be loaded`}>
          <Group justify='space-between' gap='xs'>
            <Text size='sm'>
              {isAxiosError(detail.error) &&
              detail.error.response?.status === 404
                ? t`This occurrence is no longer available to you.`
                : t`Nothing is shown in its place. Try again.`}
            </Text>
            <Button
              size='xs'
              variant='light'
              onClick={() => void detail.refetch()}
            >{t`Retry evidence`}</Button>
          </Group>
        </Alert>
      )}
      {detail.data &&
        (detail.data.snapshots.length ? (
          <Stack gap='xs'>
            {detail.data.snapshot_count > detail.data.snapshots.length && (
              <Text size='xs' c='dimmed'>
                {t`Showing the ${detail.data.snapshots.length} most recent of ${detail.data.snapshot_count} retained snapshots.`}
              </Text>
            )}
            {detail.data.snapshots.map((snapshot) => (
              <RetainedSnapshotCard key={snapshot.id} snapshot={snapshot} />
            ))}
          </Stack>
        ) : (
          <Text size='sm' c='dimmed'>
            {t`No evidence snapshot was retained for this occurrence. The record above is what was kept; today's readings are not put in its place.`}
          </Text>
        ))}
      {detail.data && extra?.(detail.data)}
      <Divider />
      <Title order={6}>{t`Work`}</Title>
      <WorkLinks occurrence={detail.data ?? occurrence} />
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
  const triggers = useRef(new Map<number, HTMLButtonElement | null>());
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
    Math.ceil((query.data?.count ?? 0) / OCCURRENCE_PAGE_SIZE)
  );
  const denied =
    isAxiosError(query.error) &&
    [401, 403, 404].includes(query.error.response?.status ?? 0);

  const toggleButton = (occurrence: FaultOccurrence) => (
    <Button
      ref={(node) => {
        triggers.current.set(occurrence.id, node);
      }}
      size='compact-xs'
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

  const evidence = (occurrence: FaultOccurrence) => (
    <div id={`occurrence-${occurrence.id}`}>
      <OccurrenceEvidence
        machineId={machineId}
        occurrence={occurrence}
        extra={renderInvestigation}
      />
    </div>
  );

  return (
    <Stack gap='sm' data-testid='fault-occurrences'>
      <Group justify='space-between' align='flex-end' gap='sm'>
        <Stack gap={2}>
          <Title order={5}>{t`Occurrences`}</Title>
          <Text size='xs' c='dimmed'>
            {t`Each alarm or detected condition recorded for this machine, with the evidence retained at the time and the work that owns the response.`}
          </Text>
        </Stack>
        <SegmentedControl
          size='xs'
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
      {query.data && (
        <Text size='sm' c='dimmed' component='output'>
          {plural(query.data.count, {
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
          {' · '}
          {t`times in your timezone (UTC${dayjs().format('Z')})`}
        </Text>
      )}
      {query.data && !query.data.results.length && (
        <Paper withBorder p='md'>
          <Text size='sm' c='dimmed'>
            {state.status === 'all'
              ? t`No alarm or detected condition has been recorded for this machine.`
              : state.status === 'active'
                ? t`No occurrence is active on this machine now.`
                : t`No resolved occurrence is recorded for this machine.`}
          </Text>
        </Paper>
      )}
      {query.data &&
        query.data.results.length > 0 &&
        (phone ? (
          <Stack gap='xs'>
            {query.data.results.map((occurrence) => (
              <Paper
                key={occurrence.id}
                withBorder
                p='sm'
                data-occurrence={occurrence.id}
              >
                <Stack gap='xs'>
                  <Group justify='space-between' gap='xs' wrap='nowrap'>
                    <SeverityBadge severity={occurrence.severity} />
                    <Outcome occurrence={occurrence} />
                  </Group>
                  <Text size='sm' fw={600} style={{ overflowWrap: 'anywhere' }}>
                    {occurrence.title}
                  </Text>
                  <EventIdentity occurrence={occurrence} />
                  <SimpleGrid cols={2} spacing='xs'>
                    <Field label={t`Occurred`}>
                      <Text size='sm'>
                        {when(occurrence.first_observed_at)}
                      </Text>
                    </Field>
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
                  {state.open === occurrence.id && evidence(occurrence)}
                </Stack>
              </Paper>
            ))}
          </Stack>
        ) : (
          <Table.ScrollContainer minWidth={960}>
            <Table
              data-testid='fault-occurrence-table'
              verticalSpacing='sm'
              style={{ tableLayout: 'fixed' }}
            >
              <Table.Thead>
                <Table.Tr>
                  <Table.Th w={170}>{t`Occurred`}</Table.Th>
                  <Table.Th w='26%'>{t`Event / finding`}</Table.Th>
                  <Table.Th>{t`Component`}</Table.Th>
                  <Table.Th>{t`Source / evidence`}</Table.Th>
                  <Table.Th>{t`Owning work`}</Table.Th>
                  <Table.Th w={150}>{t`Outcome`}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {query.data.results.map((occurrence) => (
                  <OccurrenceRows
                    key={occurrence.id}
                    occurrence={occurrence}
                    open={state.open === occurrence.id}
                    toggle={toggleButton(occurrence)}
                    evidence={
                      state.open === occurrence.id ? evidence(occurrence) : null
                    }
                  />
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ))}
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
  );
}

function OccurrenceRows({
  occurrence,
  open,
  toggle,
  evidence
}: Readonly<{
  occurrence: FaultOccurrence;
  open: boolean;
  toggle: ReactNode;
  evidence: ReactNode;
}>) {
  const top = { verticalAlign: 'top' } as const;
  return (
    <>
      <Table.Tr data-occurrence={occurrence.id}>
        <Table.Td style={top}>
          <Stack gap={2}>
            <Text size='sm'>{when(occurrence.first_observed_at)}</Text>
            {occurrence.last_observed_at !== occurrence.first_observed_at && (
              <Text size='xs' c='dimmed'>
                {t`until`} {when(occurrence.last_observed_at)}
              </Text>
            )}
            {toggle}
          </Stack>
        </Table.Td>
        <Table.Td style={{ ...top, overflowWrap: 'anywhere' }}>
          <Stack gap={4}>
            <SeverityBadge severity={occurrence.severity} />
            <Text size='sm' fw={600}>
              {occurrence.title}
            </Text>
            <EventIdentity occurrence={occurrence} />
          </Stack>
        </Table.Td>
        <Table.Td style={{ ...top, overflowWrap: 'anywhere' }}>
          <Components occurrence={occurrence} />
        </Table.Td>
        <Table.Td style={{ ...top, overflowWrap: 'anywhere' }}>
          <Evidence occurrence={occurrence} />
        </Table.Td>
        <Table.Td style={{ ...top, overflowWrap: 'anywhere' }}>
          <WorkLinks occurrence={occurrence} compact />
        </Table.Td>
        <Table.Td style={top}>
          <Outcome occurrence={occurrence} />
        </Table.Td>
      </Table.Tr>
      {open && (
        <Table.Tr>
          <Table.Td colSpan={6} bg='var(--mantine-color-default-hover)'>
            {evidence}
          </Table.Td>
        </Table.Tr>
      )}
    </>
  );
}
