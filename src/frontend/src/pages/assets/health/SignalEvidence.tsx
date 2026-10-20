import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Group,
  Loader,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Title,
  Tooltip
} from '@mantine/core';
import { IconX } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { type ReactNode, type RefObject, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { displayInstant } from '../displayClock';
import monitoring from '../monitoring.module.css';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type {
  HealthEvidenceSnapshot,
  HealthSourceStatus,
  MachineAnomaly,
  MachineSignal
} from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';
import { SignalHistoryLink } from './SignalHistoryLink';
import {
  HealthStateBadge,
  SeverityBadge,
  formatSignalLimits,
  qualityLabel,
  sourceTypeLabel,
  unassociatedAnomalyText
} from './common';
import { anomaliesForSignal, recordedAnomalyBounds } from './signalContext';
import type { PreparedTrend } from './trendSamples';

/** The id the row Inspect controls point at with aria-controls. */
export const SIGNAL_EVIDENCE_ID = 'health-signal-evidence';

function freshnessBudget(seconds: number) {
  return seconds < 60 ? t`${seconds} s` : t`${seconds / 60} min`;
}

/** A full instant with its zone, so no time is read on the wrong clock. */
export function fullInstant(value: string | number | null | undefined) {
  return displayInstant(value);
}

function Fact({
  label,
  children
}: Readonly<{ label: string; children: ReactNode }>) {
  return (
    <Stack gap={2} style={{ minWidth: 0 }}>
      <Text size='sm' className={monitoring.meta} fw={500}>
        {label}
      </Text>
      <div style={{ overflowWrap: 'anywhere' }}>{children}</div>
    </Stack>
  );
}

function Missing({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <Text size='sm' className={monitoring.meta}>
      {children}
    </Text>
  );
}

function AnomalyHistoryLink({
  anomaly,
  machineId,
  bindingId
}: Readonly<{
  anomaly: MachineAnomaly;
  machineId: number;
  bindingId: number;
}>) {
  const bounds = recordedAnomalyBounds(anomaly);
  return bounds ? (
    <SignalHistoryLink
      machineId={machineId}
      bindingId={bindingId}
      {...bounds}
      clock='recorded'
      occurrence={anomaly.pk}
    />
  ) : (
    <Missing>{t`Occurrence clock offset unavailable; source window cannot be linked.`}</Missing>
  );
}

function snapshotReason(reason: string): string {
  switch (reason) {
    case 'manual_repair':
      return t`Manual repair`;
    case 'anomaly_repair':
      return t`Anomaly repair`;
    case 'ai_diagnosis':
      return t`Diagnosis`;
    case 'confirmation_test':
      return t`Confirmation test`;
    default:
      return reason;
  }
}

function readingText(signal: MachineSignal): string | null {
  if (signal.value === null || signal.value === undefined) {
    return null;
  }
  const value =
    typeof signal.value === 'number'
      ? Number.parseFloat(signal.value.toFixed(3)).toString()
      : String(signal.value);
  return signal.unit ? `${value} ${signal.unit}` : value;
}

/**
 * Everything recorded about one signal, opened from its row or the chart
 * (R6 screen 11, A05.6, A23.1).
 *
 * Each fact says where it comes from, and a missing one says it is missing:
 * no component is guessed from a tag name, no unit is supplied for a raw
 * channel, an absent limit is not a passed one, and an anomaly with no signal
 * association is never counted as "none for this signal". The current reading
 * and any retained snapshot are labelled apart, with their own times.
 */
export function SignalEvidence({
  machineId,
  bindingId,
  signal,
  source,
  anomalies,
  prepared,
  trendAvailable,
  historyContext,
  headingRef,
  focusToken,
  onClose
}: Readonly<{
  machineId: number;
  bindingId: number;
  signal: MachineSignal | undefined;
  source: HealthSourceStatus | undefined;
  anomalies: MachineAnomaly[] | null;
  /** The chart's reading of its current window, for the window label. */
  prepared: PreparedTrend | null;
  trendAvailable: boolean;
  historyContext?: ReactNode;
  headingRef: RefObject<HTMLHeadingElement | null>;
  /** Changes each time the panel is opened from a trigger, to move focus. */
  focusToken: number;
  onClose: () => void;
}>) {
  const api = useApi();

  // One read of the machine's snapshots, only while the panel is open, and
  // filtered here by binding: no request per row.
  const snapshotsQuery = useQuery<HealthEvidenceSnapshot[]>({
    queryKey: ['machine-health-snapshots', machineId],
    staleTime: 60 * 1000,
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_snapshots, machineId)
      );
      return response.data?.results ?? [];
    }
  });

  useEffect(() => {
    if (focusToken > 0) {
      headingRef.current?.focus();
    }
  }, [focusToken, headingRef]);

  const context = anomaliesForSignal(anomalies ?? [], bindingId);
  const snapshots = (snapshotsQuery.data ?? []).filter(
    (snapshot) => snapshot.binding === bindingId
  );

  const name = signal?.display_name ?? t`Signal ${bindingId}`;
  const reading = signal ? readingText(signal) : null;
  const limits = signal ? formatSignalLimits(signal) : null;

  return (
    <Paper
      withBorder
      p='md'
      id={SIGNAL_EVIDENCE_ID}
      component='section'
      aria-labelledby={`${SIGNAL_EVIDENCE_ID}-title`}
    >
      <Stack gap='md'>
        <Group justify='space-between' align='flex-start' wrap='nowrap'>
          <Title
            order={5}
            id={`${SIGNAL_EVIDENCE_ID}-title`}
            ref={headingRef}
            tabIndex={-1}
            style={{ overflowWrap: 'anywhere' }}
          >
            {t`Signal evidence: ${name}`}
          </Title>
          <Tooltip label={t`Close signal evidence`}>
            <ActionIcon
              variant='subtle'
              color='gray'
              aria-label={t`Close signal evidence`}
              onClick={onClose}
            >
              <IconX size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>

        {!signal ? (
          <Alert color='gray' title={t`This signal is not mapped here`}>
            {t`No active mapping on this machine has that signal, so nothing about it can be shown.`}
          </Alert>
        ) : (
          <SimpleGrid cols={{ base: 1, xs: 2, lg: 3 }} spacing='md'>
            <Fact label={t`Source tag`}>
              <Text size='sm' ff='monospace'>
                {signal.external_key}
              </Text>
            </Fact>
            <Fact label={t`Component`}>
              {signal.component ? (
                <Group gap={6} wrap='wrap'>
                  <Text size='sm'>{signal.component.name}</Text>
                  <Text size='sm' className={monitoring.meta} ff='monospace'>
                    {signal.component.code}
                  </Text>
                  <Badge
                    size='sm'
                    variant='outline'
                    color={
                      signal.component.status === 'verified' ? 'teal' : 'gray'
                    }
                  >
                    {signal.component.status === 'verified'
                      ? t`Verified assignment`
                      : t`Draft inference`}
                  </Badge>
                </Group>
              ) : (
                <Missing>{t`Not recorded in the tag dictionary`}</Missing>
              )}
            </Fact>
            <Fact label={t`Unit and kind`}>
              <Text size='sm'>
                {signal.unit
                  ? signal.unit
                  : t`No unit recorded - a raw source value`}
                {signal.signal_kind ? ` · ${signal.signal_kind}` : ''}
              </Text>
            </Fact>
            <Fact label={t`Latest recorded reading`}>
              {reading ? (
                <Group gap={6} wrap='wrap'>
                  <Text size='sm' fw={500}>
                    {reading}
                  </Text>
                  <Badge size='sm' variant='light' color='gray'>
                    {signal.stale ? t`Last-known` : t`Current`}
                  </Badge>
                </Group>
              ) : (
                <Missing>{t`No reading`}</Missing>
              )}
            </Fact>
            <Fact label={t`Observed (dashboard clock)`}>
              {fullInstant(signal.observed_at) ? (
                <Text size='sm'>{fullInstant(signal.observed_at)}</Text>
              ) : (
                <Missing>{t`No observation time`}</Missing>
              )}
            </Fact>
            <Fact label={t`Received by AIMMS`}>
              {fullInstant(signal.received_at) ? (
                <Text size='sm'>{fullInstant(signal.received_at)}</Text>
              ) : (
                <Missing>{t`Not recorded`}</Missing>
              )}
            </Fact>
            <Fact label={t`Freshness`}>
              <Text size='sm'>
                {!signal.observed_at ||
                !Number.isFinite(Date.parse(signal.observed_at))
                  ? t`No observation time; freshness unavailable`
                  : Date.parse(signal.observed_at) > Date.now()
                    ? t`Observation dated after now; not current`
                    : signal.stale
                      ? t`Stale: older than the ${freshnessBudget(signal.freshness_threshold_seconds)} freshness budget`
                      : t`Within the ${freshnessBudget(signal.freshness_threshold_seconds)} freshness budget`}
              </Text>
            </Fact>
            <Fact label={t`Quality`}>
              <Text size='sm'>
                {t`${qualityLabel(signal.quality)}, as the source reported it`}
              </Text>
            </Fact>
            <Fact label={t`State`}>
              <HealthStateBadge state={signal.state} size='sm' />
            </Fact>
            <Fact label={t`Critical designation`}>
              <Text size='sm'>
                {signal.critical_designation?.review_required ||
                signal.critical_designation?.applies_current === false
                  ? t`Designation review required — binding meaning changed`
                  : signal.critical_designation?.critical
                    ? t`Designated critical`
                    : t`Not designated`}
              </Text>
              {signal.critical_designation && (
                <Text size='sm' className={monitoring.meta}>
                  {signal.critical_designation.reason} ·{' '}
                  {signal.critical_designation.recorded_by ?? t`Not recorded`} ·{' '}
                  {fullInstant(signal.critical_designation.recorded_at)}
                </Text>
              )}
              <Anchor
                className={monitoring.link}
                component={Link}
                to='/machines/registry/'
                size='sm'
              >{t`Review designations in registry`}</Anchor>
            </Fact>
            <Fact label={t`Limits`}>
              {limits ? (
                <Stack gap={0}>
                  <Text size='sm'>{limits}</Text>
                  <Text size='sm' className={monitoring.meta}>
                    {t`Configured on this mapping in AIMMS`}
                  </Text>
                </Stack>
              ) : (
                <Missing>
                  {t`Not configured - no applicable limit, which does not make the reading normal`}
                </Missing>
              )}
            </Fact>
            <Fact label={t`Source`}>
              <Stack gap={0}>
                <Text size='sm'>
                  {signal.source_name}
                  {signal.source_type
                    ? ` · ${sourceTypeLabel(signal.source_type)}`
                    : ''}
                </Text>
                {source ? (
                  <Text size='sm' className={monitoring.meta}>
                    {source.healthy
                      ? t`Connected; last success ${fullInstant(source.last_success_at) ?? t`never`}`
                      : source.active
                        ? t`Failing; last success ${fullInstant(source.last_success_at) ?? t`never`}`
                        : t`Disabled`}
                  </Text>
                ) : (
                  <Text size='sm' className={monitoring.meta}>
                    {t`Connection status not reported`}
                  </Text>
                )}
              </Stack>
            </Fact>
            <Fact label={t`Chart window`}>
              {historyContext}
              {prepared && trendAvailable ? (
                <Text size='sm'>
                  {t`Selected history window, read from the source: ${fullInstant(prepared.start) ?? '—'} to ${fullInstant(prepared.end) ?? '—'}`}
                </Text>
              ) : (
                <Missing>{t`No history read for this signal`}</Missing>
              )}
            </Fact>
          </SimpleGrid>
        )}

        <Stack gap={6}>
          <Text size='sm' fw={600}>
            {t`Active anomalies citing this signal`}
          </Text>
          {anomalies === null ? (
            <Missing>{t`Active anomaly read incomplete or failed; association unavailable.`}</Missing>
          ) : null}
          {anomalies !== null &&
          context.cited.length === 0 &&
          context.unassociated.length === 0 ? (
            <Missing>{t`No active anomaly cites this signal.`}</Missing>
          ) : null}
          {context.cited.length === 0 && context.unassociated.length > 0 ? (
            <Missing>{t`None cites it by its signal.`}</Missing>
          ) : null}
          {context.cited.map((anomaly) => (
            <Group key={anomaly.pk} gap='xs' wrap='wrap'>
              <SeverityBadge severity={anomaly.severity} />
              <Text size='sm'>{anomaly.title}</Text>
              <Text size='sm' className={monitoring.meta}>
                {t`First observed ${fullInstant(anomaly.first_observed_at) ?? '—'}`}
              </Text>
              <AnomalyHistoryLink
                machineId={machineId}
                bindingId={bindingId}
                anomaly={anomaly}
              />
              {anomaly.work_order ? (
                <Anchor
                  className={monitoring.link}
                  component={Link}
                  to={`/maintenance/work-orders/${anomaly.work_order}/`}
                  size='sm'
                >
                  {t`Open owning work order`}
                </Anchor>
              ) : (
                <Text size='sm' className={monitoring.meta}>
                  {t`No owning work recorded`}
                </Text>
              )}
            </Group>
          ))}
          {context.unassociated.length > 0 ? (
            <Text size='sm' className={monitoring.meta}>
              {unassociatedAnomalyText(context.unassociated.length)}
            </Text>
          ) : null}
        </Stack>

        <Stack gap={6}>
          <Text size='sm' fw={600}>
            {t`Retained evidence`}
          </Text>
          {snapshotsQuery.isLoading ? <Loader size='sm' /> : null}
          {snapshotsQuery.isError ? (
            <Text size='sm' className={monitoring.meta}>
              {t`Retained evidence could not be read. That is not an absence of snapshots.`}
            </Text>
          ) : null}
          {snapshotsQuery.isSuccess && snapshots.length === 0 ? (
            <Missing>{t`No snapshot of this signal in the latest 50 machine snapshots.`}</Missing>
          ) : null}
          {snapshots.slice(0, 5).map((snapshot) => (
            <Stack key={snapshot.id} gap={0}>
              <Group gap={6} wrap='wrap'>
                <Badge size='sm' variant='light' color='violet'>
                  {t`Retained snapshot`}
                </Badge>
                <Text size='sm'>
                  {t`Captured ${fullInstant(snapshot.captured_at) ?? '—'} for ${snapshotReason(snapshot.reason)}`}
                  <Anchor
                    className={monitoring.link}
                    component={Link}
                    to={`/machines/machine/${machineId}/health/?snapshot=${snapshot.id}`}
                    size='sm'
                  >{t`Open exact retained snapshot`}</Anchor>
                </Text>
              </Group>
              <SignalHistoryLink
                machineId={machineId}
                bindingId={bindingId}
                from={snapshot.window_start}
                to={snapshot.window_end}
                clock='recorded'
                occurrence={snapshot.anomaly}
                snapshot={snapshot.id}
              />
              <Text size='sm' className={monitoring.meta}>
                {t`Window ${fullInstant(snapshot.window_start) ?? '—'} to ${fullInstant(snapshot.window_end) ?? '—'}`}
                {' · '}
                {qualityLabel(snapshot.quality)}
                {snapshot.stale ? ` · ${t`stale when captured`}` : ''}
                {snapshot.anomaly ? ` · ${t`anomaly ${snapshot.anomaly}`}` : ''}
                {' · '}
                <Text span ff='monospace' size='sm'>
                  {snapshot.content_hash.slice(0, 12)}
                </Text>
              </Text>
            </Stack>
          ))}
          {snapshots.length > 5 ? (
            <Text size='sm' className={monitoring.meta}>
              {t`${snapshots.length - 5} older snapshots are not listed.`}
            </Text>
          ) : null}
        </Stack>
      </Stack>
    </Paper>
  );
}

export default SignalEvidence;
