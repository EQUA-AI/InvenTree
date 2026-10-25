import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Button,
  Divider,
  Group,
  Paper,
  Skeleton,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { IconFlag } from '@tabler/icons-react';
import type { UseQueryResult } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { Link } from 'react-router-dom';
import { ComponentBasis } from '../../../tables/assets/ComponentFacts';
import { FreshnessLabel } from '../health/mimic/FreshnessLabel';
import { useDisplayClock } from '../health/mimic/observedTime';
import { machineOpenWorkHref } from '../maintenance/machineWorkViews';
import operations from '../operations.module.css';

import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { getDetailUrl } from '@lib/functions/Navigation';
import { useUserState } from '../../../states/UserState';

import type { SignalQuality } from '@lib/types/MachineHealth';
import { FittedItemFact } from '../FittedIdentity';
import {
  StatusBadge as Badge,
  HealthStateBadge,
  qualityLabel
} from '../health/common';
import { formatInstant, formatValue, unitLabel } from '../performance/format';
import type {
  MachinePositions,
  PositionComponent,
  PositionSignal,
  PositionWorkRow
} from './machinePositions';
import { positionSummary } from './machinePositions';
import { referencePartLabel } from './referenceParts';
import type { TwinPartId } from './twinScene.mjs';

const PRIORITY_COLORS: Record<PositionWorkRow['priority'], string> = {
  high: 'red',
  medium: 'orange',
  low: 'gray'
};

function priorityLabel(priority: PositionWorkRow['priority']): string {
  switch (priority) {
    case 'high':
      return t`High`;
    case 'medium':
      return t`Medium`;
    case 'low':
      return t`Low`;
  }
}

function Section({
  title,
  children
}: Readonly<{ title: string; children: React.ReactNode }>) {
  return (
    <Stack gap={4}>
      <Text size='xs' fw={600} className={operations.meta} tt='none'>
        {title}
      </Text>
      {children}
    </Stack>
  );
}

function Absent({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <Text size='sm' className={operations.meta}>
      {children}
    </Text>
  );
}

function ComponentSlot({ slot }: Readonly<{ slot: PositionComponent }>) {
  const user = useUserState();
  return (
    <Stack gap={2} data-slot={slot.code}>
      <Group gap='xs' wrap='nowrap'>
        <Text size='sm' fw={500}>
          {slot.name}
        </Text>
        <ComponentBasis
          record={{ ...slot, review_note: slot.review_note ?? '' }}
        />
      </Group>
      {user.hasViewRole(UserRoles.stock) && slot.fitted?.current && (
        <FittedItemFact fit={slot.fitted.current} />
      )}
      {/* The catalogue family stays separate from the recorded fitted instance. */}
      {user.hasViewRole(UserRoles.part) && (
        <Anchor
          className={operations.link}
          component={Link}
          to={getDetailUrl(ModelType.part, slot.part.pk)}
          size='xs'
          aria-label={t`Open part record: ${slot.part.name}`}
        >
          {t`Open part record`}
        </Anchor>
      )}
    </Stack>
  );
}

function readingText(signal: PositionSignal): string {
  if (signal.value === null || signal.value === undefined) return t`No reading`;
  // Numbers at the precision the Performance tab uses; anything else (a
  // status word, a flag) exactly as the source sent it.
  return typeof signal.value === 'number'
    ? formatValue(signal.value, 2, signal.unit || undefined)
    : String(signal.value);
}

function Reading({ signal }: Readonly<{ signal: PositionSignal }>) {
  const exercise = signal.test_alarms?.find(
    (alarm) =>
      ['open', 'acknowledged'].includes(alarm.status) &&
      Number.isFinite(alarm.test_value) &&
      unitLabel(alarm.unit) === unitLabel(signal.unit)
  );
  return (
    <Group
      justify='space-between'
      gap='xs'
      wrap='nowrap'
      align='flex-start'
      data-signal={signal.binding_id}
    >
      <Stack gap={0} style={{ minWidth: 0 }}>
        <Text size='xs' className={operations.meta} truncate>
          {signal.display_name}
        </Text>
        {/* The value leads; its observation time follows, as on Health. */}
        <Text size='sm' fw={600}>
          {exercise ? (
            <Text
              component='span'
              c='light-dark(var(--mantine-color-red-9), var(--mantine-color-red-3))'
              data-position-test-value
            >
              {t`TEST`} {formatValue(exercise.test_value, 2, exercise.unit)}
            </Text>
          ) : (
            readingText(signal)
          )}
        </Text>
        {exercise && (
          <>
            <Text size='xs'>
              {t`Measured`}:{' '}
              {signal.quality === 'good' && !signal.stale
                ? readingText(signal)
                : '—'}
            </Text>
            <Text size='xs' data-position-test-context>
              {exercise.status === 'acknowledged' ? t`Acknowledged` : t`Open`}
              {' · '}
              {formatInstant(Date.parse(exercise.triggered_at))}
            </Text>
          </>
        )}
        <FreshnessLabel
          point={{
            observed_at: signal.observed_at,
            age_seconds: signal.age_seconds ?? null,
            reason: signal.stale ? 'stale' : null
          }}
        />
        {signal.quality !== 'good' && (
          <Text
            size='xs'
            className={operations.meta}
            data-quality={signal.quality}
          >
            {t`Quality`}: {qualityLabel(signal.quality as SignalQuality)}
          </Text>
        )}
      </Stack>
      <Stack gap={4} align='flex-end'>
        {exercise && (
          <Badge color='red.9' size='xs'>{t`TEST alarm — Critical`}</Badge>
        )}
        {(!exercise || signal.state !== 'normal') && (
          <HealthStateBadge state={signal.state} size='xs' />
        )}
      </Stack>
    </Group>
  );
}

function WorkRows({ rows }: Readonly<{ rows: PositionWorkRow[] }>) {
  return (
    <Stack gap={6}>
      {rows.map((row) => (
        <Group key={row.id} gap='xs' wrap='nowrap' align='flex-start'>
          <Badge
            size='xs'
            variant='outline'
            leftSection={<IconFlag size={12} />}
            color={PRIORITY_COLORS[row.priority]}
            style={{ flexShrink: 0, marginTop: 3 }}
          >
            {priorityLabel(row.priority)}
          </Badge>
          <Stack gap={0} style={{ minWidth: 0 }}>
            <Anchor
              className={operations.link}
              component={Link}
              to={`/maintenance/work-orders/${row.id}/`}
              size='sm'
              fw={500}
            >
              {row.reference ?? t`Work order ${row.id}`}
            </Anchor>
            <Text size='xs' className={operations.meta} lineClamp={2}>
              {row.title}
            </Text>
          </Stack>
        </Group>
      ))}
    </Stack>
  );
}

/**
 * The machine's work as context. Beside a position's own work it is the
 * rest ("other"); for a position with none it is all of it - so the two
 * numbers never appear to contradict the machine's own count.
 */
function ParentWork({
  count,
  machineId,
  other = false
}: Readonly<{ count: number | null; machineId: number; other?: boolean }>) {
  return (
    <Anchor
      className={operations.link}
      component={Link}
      to={machineOpenWorkHref(`/machines/machine/${machineId}/`)}
      size='sm'
      data-parent-work={count ?? 'unknown'}
    >
      {count === null
        ? t`Machine work could not be checked.`
        : other
          ? t`${count} other open on this machine (parent context, not this position's).`
          : t`${count} open on the whole machine (parent context, not this position's).`}
    </Anchor>
  );
}

/**
 * R6 P07: the selected position's own records, from the machine's component
 * registry (`reference_positions.v1`). Each absence is said in its own words
 * - no family, no slot, no signal, no work, work unknown - and nothing is
 * borrowed from a neighbouring position. Machine work is parent context.
 */
export function PositionInspector({
  machineId,
  selected,
  query
}: Readonly<{
  machineId: number;
  selected: TwinPartId;
  query: UseQueryResult<MachinePositions>;
}>) {
  const clock = useDisplayClock();
  const summary = positionSummary(query.data, selected);
  const links = (
    <Group gap='xs'>
      <Button
        component={Link}
        to={`/machines/machine/${machineId}/health/`}
        size='compact-xs'
        variant='subtle'
        className={operations.link}
      >
        {t`View Health`}
      </Button>
      <Button
        component={Link}
        to={`/machines/machine/${machineId}/parts/`}
        size='compact-xs'
        variant='subtle'
        className={operations.link}
      >
        {t`View Parts`}
      </Button>
    </Group>
  );

  return (
    <Paper
      component='section'
      withBorder
      radius='md'
      p='sm'
      aria-label={t`Selected position`}
    >
      <Stack gap='sm'>
        <Stack gap={2}>
          <Title order={5}>{referencePartLabel(selected)}</Title>
        </Stack>
        <Divider />
        {query.isPending ? (
          <Stack
            component='output'
            gap='xs'
            aria-label={t`Loading component records`}
          >
            <Skeleton h={14} />
            <Skeleton h={14} w='70%' />
            <Skeleton h={14} w='50%' />
          </Stack>
        ) : isAxiosError(query.error) &&
          [401, 403, 404].includes(query.error.response?.status ?? 0) ? (
          // Access, not a fault: retrying cannot change it.
          <Alert
            color='gray'
            variant='light'
            title={t`Records not available to you`}
          >
            <Text size='sm'>
              {t`Your access does not include this machine's component records.`}
            </Text>
          </Alert>
        ) : query.isError || !summary ? (
          <Alert color='red' variant='light' title={t`Records unavailable`}>
            <Stack gap='xs'>
              <Text size='sm'>
                {t`This machine's component records could not be loaded. Nothing cached is shown.`}
              </Text>
              <Button
                size='compact-xs'
                variant='light'
                w='fit-content'
                onClick={() => void query.refetch()}
              >
                {t`Retry`}
              </Button>
            </Stack>
          </Alert>
        ) : summary.kind === 'machine' ? (
          <Stack gap='sm'>
            <Section title={t`Work`}>
              <Anchor
                className={operations.link}
                component={Link}
                to={machineOpenWorkHref(`/machines/machine/${machineId}/`)}
                size='sm'
                data-machine-work={summary.machineWork ?? 'unknown'}
              >
                {summary.machineWork === null
                  ? t`Work could not be checked.`
                  : t`${summary.machineWork} open on this machine`}
              </Anchor>
            </Section>

            {links}
          </Stack>
        ) : summary.kind === 'unbound' ? (
          <Stack gap='sm'>
            <Absent>
              {t`Not bound: this reference position has no catalogue component family, so no records are shown for it.`}
            </Absent>
            <ParentWork machineId={machineId} count={summary.machineWork} />
          </Stack>
        ) : summary.kind === 'no-slot' ? (
          <Stack gap='sm'>
            <Absent>
              {t`No ${summary.familyName ?? summary.family} component is recorded for this machine, so no readings or work are shown.`}
            </Absent>
            <ParentWork machineId={machineId} count={summary.machineWork} />
            {links}
          </Stack>
        ) : (
          <Stack gap='sm'>
            <Section title={t`Component`}>
              {summary.position.components.map((slot) => (
                <ComponentSlot key={slot.code} slot={slot} />
              ))}
            </Section>
            <Section title={t`Readings`}>
              {summary.position.signals.length ? (
                summary.position.signals.map((signal) => (
                  <Reading key={signal.binding_id} signal={signal} />
                ))
              ) : (
                <Absent>{t`No mapped signal`}</Absent>
              )}
            </Section>
            <Section title={t`Work`}>
              {summary.position.work === null ? (
                <Absent>{t`Work could not be checked.`}</Absent>
              ) : summary.position.work.count ? (
                <>
                  <Text
                    size='sm'
                    data-position-work={summary.position.work.count}
                  >
                    {t`${summary.position.work.count} open on this component`}
                  </Text>
                  <WorkRows rows={summary.position.work.results} />
                </>
              ) : (
                <Absent>{t`No open work on this component`}</Absent>
              )}
              <ParentWork
                machineId={machineId}
                count={summary.parentWork}
                other
              />
            </Section>
            {links}
          </Stack>
        )}
      </Stack>
    </Paper>
  );
}
