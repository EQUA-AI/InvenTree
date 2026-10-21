import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Box,
  Button,
  Group,
  Loader,
  Paper,
  Stack,
  Text,
  Title
} from '@mantine/core';
import {
  IconCloudOff,
  IconPlayerPause,
  IconPlugConnectedX
} from '@tabler/icons-react';
import { useContext } from 'react';
import { Link } from 'react-router-dom';
import { EquipmentViewControls } from '../equipment/ViewControls';
import operations from '../operations.module.css';
import { StatusBadge } from './common';
import { AlarmTable } from './mimic/AlarmTable';
import { UnitMimic, UnitMimicHeader } from './mimic/UnitMimic';
import { displayFor, pollErrorLabel } from './mimic/format';
import {
  DisplayClockContext,
  formatInstant,
  useDisplayClock
} from './mimic/observedTime';
import { useStationMimic } from './useStationMimic';

export default function PumpMimic({
  stationId,
  unit
}: Readonly<{ stationId: number; unit: string }>) {
  const { ref, query } = useStationMimic(stationId, unit);
  const data = query.data;
  const clock = useDisplayClock(data?.plant_timezone);
  const controls = useContext(EquipmentViewControls);
  const bay = data?.bays.find((b) => b.key === unit);
  const alarms = bay?.machine
    ? (data?.alarms ?? []).filter((a) => a.machine === bay.machine)
    : [];
  const activeAlarms = alarms.filter(
    (alarm) =>
      alarm.anomaly !== null &&
      ['open', 'acknowledged'].includes(alarm.anomaly_status ?? '')
  );
  const critical = activeAlarms.filter(
    (alarm) => alarm.severity === 'critical'
  ).length;
  const warning = activeAlarms.filter(
    (alarm) => alarm.severity === 'warning'
  ).length;
  const other = activeAlarms.length - critical - warning;
  // The mimic lists supplied breach details; a standing bay alarm can outlive
  // a usable reading and therefore have no row in this response.
  const missingAlarmDetails =
    !!bay?.alarm && !activeAlarms.some((alarm) => alarm.severity === bay.alarm);
  const worst =
    bay?.alarm === 'critical' || critical > 0
      ? 'critical'
      : bay?.alarm === 'warning' || warning > 0
        ? 'warning'
        : null;
  const worstLabel = worst === 'critical' ? t`Critical` : t`Warning`;
  return (
    <DisplayClockContext value={clock}>
      <Stack ref={ref} gap='sm' mih={160} className={operations.phoneStack}>
        <Paper
          component='section'
          aria-labelledby='schematic-title'
          className={operations.band}
        >
          <Group
            justify='space-between'
            gap='xs'
            className={operations.pumpStatus}
          >
            {data && !query.isError ? (
              <UnitMimicHeader
                data={data}
                unit={unit}
                headingId='schematic-title'
              />
            ) : (
              <Title
                order={4}
                id='schematic-title'
              >{t`Process readings`}</Title>
            )}
            {data && !query.isError && (
              <Group
                gap='xs'
                aria-label={t`Active alarm summary`}
                component='fieldset'
                bd={0}
                p={0}
                m={0}
                miw={0}
                className={operations.pumpAlarmSummary}
              >
                {!bay?.machine ? (
                  <StatusBadge
                    color='gray'
                    className={operations.pumpAlarmCounts}
                    miw={0}
                  >{t`Active alarms unavailable · pump mapping not supplied`}</StatusBadge>
                ) : missingAlarmDetails ? (
                  <StatusBadge
                    color={worst === 'critical' ? 'red' : 'yellow'}
                    className={operations.pumpAlarmCounts}
                    miw={0}
                  >
                    {t`Active alarm`} · {worstLabel} · {t`Count unavailable`}
                  </StatusBadge>
                ) : (
                  <StatusBadge
                    color={
                      worst === 'critical'
                        ? 'red'
                        : worst === 'warning'
                          ? 'yellow'
                          : 'gray'
                    }
                    className={operations.pumpAlarmCounts}
                    miw={0}
                  >
                    <Box component='span' visibleFrom='sm'>
                      {t`Active alarms shown`}: {activeAlarms.length} ·{' '}
                      {t`Critical`}: {critical} · {t`Warning`}: {warning}
                    </Box>
                    <Box component='span' hiddenFrom='sm'>
                      <Box component='span' display='block'>
                        {t`Active alarms shown`}: {activeAlarms.length}
                      </Box>
                      <Box component='span' display='block'>
                        {t`Critical`}: {critical} · {t`Warning`}: {warning}
                      </Box>
                    </Box>
                  </StatusBadge>
                )}
                {other > 0 && (
                  <Text size='sm'>
                    {t`Severity unavailable`}: {other}
                  </Text>
                )}
                <Anchor
                  href='#pump-threshold-alarms'
                  className={`${operations.link} ${operations.phoneAlarmLink}`}
                  onClick={(event) => {
                    event.preventDefault();
                    const target = document.getElementById(
                      'pump-threshold-alarms'
                    );
                    target?.focus({ preventScroll: true });
                    target?.scrollIntoView({ block: 'start' });
                  }}
                >{t`Open alarms`}</Anchor>
              </Group>
            )}
            <Group gap='sm' className={operations.pumpActions}>
              {controls}
              <Button
                variant='default'
                size='xs'
                loading={query.isFetching}
                onClick={() => query.refetch()}
              >{t`Refresh`}</Button>
              <Anchor
                className={operations.link}
                component={Link}
                to={`/machines/machine/${stationId}/mimic`}
                size='sm'
              >{t`Open station mimic`}</Anchor>
            </Group>
          </Group>
          <Text
            className={operations.caveat}
          >{t`Reference schematic · physical arrangement unverified`}</Text>
          <Text className={operations.meta} component='output'>
            {t`Source`}:{' '}
            {data?.source?.name ?? t`Unavailable · source not supplied`} ·{' '}
            {t`Last poll attempt`}:{' '}
            {formatInstant(data?.last_poll_at, clock) ??
              (data && !query.isError
                ? t`none recorded`
                : t`time not supplied`)}{' '}
            · {t`Read at`}{' '}
            {formatInstant(data?.generated_at, clock) ?? t`Time unavailable`} ·{' '}
            {t`Times`}: {clock.label}
          </Text>
          {query.isError && (
            <Alert
              color='gray'
              icon={<IconCloudOff />}
              role='alert'
              title={t`Readings unavailable`}
            >{t`Refresh to retry; cached values are hidden.`}</Alert>
          )}
          {!query.isError && data?.last_error_code && (
            <Alert
              color='gray'
              variant='light'
              icon={<IconPlugConnectedX />}
              role='alert'
              title={t`Polling error`}
              c='var(--mantine-color-text)'
            >
              {pollErrorLabel(data.last_error_code)} (code{' '}
              {data.last_error_code}). {t`Last attempt`}{' '}
              {formatInstant(data.last_poll_at, clock) ?? t`time not supplied`}.{' '}
              {t`Readings are shown only while they are within the source's freshness limit.`}{' '}
              {t`Source last error`}:{' '}
              {formatInstant(data.last_error_at, clock) ?? t`time not supplied`}{' '}
              · {t`Source last success`}:{' '}
              {formatInstant(data.last_success_at, clock) ??
                t`time not supplied`}
            </Alert>
          )}
          {!query.isError && data && !data.enabled && (
            <Alert
              color='gray'
              icon={<IconPlayerPause />}
              c='var(--mantine-color-text)'
            >{t`Polling is disabled for this station. Live values are unavailable.`}</Alert>
          )}
          {data && data.layout.review_status !== 'approved' && (
            <Group role='note' gap='xs'>
              <StatusBadge
                variant='outline'
                color='gray'
              >{t`Layout awaiting plant review`}</StatusBadge>
              <Text
                className={operations.meta}
              >{t`Missing mappings are shown as unavailable.`}</Text>
            </Group>
          )}
        </Paper>
        {query.isError ? null : !data ? (
          <Loader />
        ) : (
          <>
            <UnitMimic
              header={false}
              data={data}
              unit={unit}
              actions={
                <Anchor
                  className={operations.link}
                  component={Link}
                  to={`/machines/machine/${stationId}/mimic`}
                  size='sm'
                >{t`Open station mimic`}</Anchor>
              }
            />
            <Title
              order={5}
              id='pump-threshold-alarms'
              tabIndex={-1}
            >{t`Threshold alarms`}</Title>
            {missingAlarmDetails && bay?.machine && (
              <Text size='sm'>{t`Active alarm details are unavailable in this response. The bay's active severity remains recorded.`}</Text>
            )}
            {!bay?.machine ? (
              <Text>{t`Alarm scope unavailable for this pump.`}</Text>
            ) : alarms.length ? (
              <AlarmTable
                alarms={alarms}
                display={displayFor(data, unit)}
                onAcknowledged={() => query.refetch()}
              />
            ) : (
              <Text>
                {missingAlarmDetails
                  ? t`Active alarm details unavailable.`
                  : bay.alarm === null
                    ? t`No active alarms from configured thresholds.`
                    : t`No active alarm details supplied.`}
              </Text>
            )}
          </>
        )}
      </Stack>
    </DisplayClockContext>
  );
}
