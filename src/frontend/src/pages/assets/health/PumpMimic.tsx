import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Anchor,
  Group,
  Loader,
  Paper,
  Stack,
  Text,
  Title,
  Tooltip
} from '@mantine/core';
import {
  IconCloudOff,
  IconInfoCircle,
  IconPlayerPause,
  IconPlugConnectedX
} from '@tabler/icons-react';
import { useContext } from 'react';
import { Link } from 'react-router-dom';
import { RefreshAction } from '../../../components/buttons/RefreshAction';
import { MissingValue } from '../../../components/items/ValueTooltip';
import { EquipmentViewControls } from '../equipment/ViewControls';
import operations from '../operations.module.css';
import { recordedTelemetryAsLive } from '../telemetryPresentation';
import { StatusBadge } from './common';
import { AlarmTable } from './mimic/AlarmTable';
import {
  MimicAlarmCoverage,
  unconfiguredCount
} from './mimic/MimicAlarmCoverage';
import { MimicMotion, MimicMotionControl } from './mimic/MimicMotion';
import { TestAlarmNotice } from './mimic/TestAlarmNotice';
import { UnitMimic, UnitMimicHeader } from './mimic/UnitMimic';
import { displayFor, pollErrorLabel } from './mimic/format';
import { knownNoActiveAlarms } from './mimic/mimicVisuals';
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
  const knownEmptyAlarms = knownNoActiveAlarms(data);
  const worst =
    bay?.alarm === 'critical' || critical > 0
      ? 'critical'
      : bay?.alarm === 'warning' || warning > 0
        ? 'warning'
        : null;
  const worstLabel = worst === 'critical' ? t`Critical` : t`Warning`;
  const currentSource =
    recordedTelemetryAsLive(data) && data?.reporting != null;
  const provenance = `${t`Source`}: ${data?.source?.name ?? t`Source not supplied`} · ${currentSource ? t`Last reading` : t`Last poll attempt`}: ${formatInstant(currentSource ? data?.last_observed_at : data?.last_poll_at, clock) ?? t`Reading time not supplied`} · ${t`Times`}: ${clock.label}`;
  return (
    <DisplayClockContext value={clock}>
      <MimicMotion>
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
                  information={
                    <Tooltip
                      label={`${
                        data && data.layout.review_status !== 'approved'
                          ? t`Layout pending plant approval`
                          : t`Schematic layout`
                      }\n${provenance}`}
                      multiline
                      maw={360}
                      events={{ hover: true, focus: true, touch: true }}
                    >
                      <ActionIcon
                        variant='subtle'
                        color='gray'
                        aria-label={t`Schematic information`}
                        className={operations.phoneTarget}
                      >
                        <IconInfoCircle size={18} />
                      </ActionIcon>
                    </Tooltip>
                  }
                />
              ) : (
                <Group gap='xs'>
                  <Title
                    order={4}
                    id='schematic-title'
                  >{t`Process readings`}</Title>
                </Group>
              )}

              {data &&
                !query.isError &&
                (!bay?.machine ||
                  missingAlarmDetails ||
                  activeAlarms.length > 0) && (
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
                        multiline
                        color='gray'
                        className={operations.pumpAlarmCounts}
                        miw={0}
                      >{t`Active alarms unavailable · pump mapping not supplied`}</StatusBadge>
                    ) : missingAlarmDetails ? (
                      <StatusBadge
                        multiline
                        color={worst === 'critical' ? 'red' : 'yellow'}
                        className={operations.pumpAlarmCounts}
                        miw={0}
                      >
                        {t`Active alarm`} · {worstLabel} ·{' '}
                        {t`Count unavailable`}
                      </StatusBadge>
                    ) : (
                      <Tooltip
                        label={`${t`Active alarms shown`}: ${activeAlarms.length}`}
                      >
                        <Group
                          component='fieldset'
                          bd={0}
                          m={0}
                          p={0}
                          miw={0}
                          gap={6}
                          aria-label={`${t`Active alarms shown`}: ${activeAlarms.length}`}
                        >
                          {critical > 0 && (
                            <StatusBadge color='red'>
                              {t`Critical`} · {critical}
                            </StatusBadge>
                          )}
                          {warning > 0 && (
                            <StatusBadge color='yellow'>
                              {t`Warning`} · {warning}
                            </StatusBadge>
                          )}
                        </Group>
                      </Tooltip>
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
                {data && !query.isError && (
                  <MimicAlarmCoverage
                    count={unconfiguredCount(Object.values(data.points))}
                    knownEmpty={knownEmptyAlarms}
                  />
                )}
                {controls}
                <MimicMotionControl />
                <RefreshAction
                  loading={query.isFetching}
                  onClick={() => query.refetch()}
                />
                <Anchor
                  className={`${operations.link} ${operations.phoneTarget}`}
                  component={Link}
                  to={`/machines/machine/${stationId}/mimic`}
                  style={{ display: 'inline-flex', alignItems: 'center' }}
                  size='sm'
                >{t`Open station mimic`}</Anchor>
              </Group>
            </Group>

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
                {formatInstant(data.last_poll_at, clock) ?? (
                  <MissingValue
                    label={t`Last attempt`}
                    reason={t`time not supplied`}
                  />
                )}
                .{' '}
                {t`Readings are shown only while they are within the source's freshness limit.`}{' '}
                {t`Source last error`}:{' '}
                {formatInstant(data.last_error_at, clock) ?? (
                  <MissingValue
                    label={t`Source last error`}
                    reason={t`time not supplied`}
                  />
                )}{' '}
                · {t`Source last success`}:{' '}
                {formatInstant(data.last_success_at, clock) ?? (
                  <MissingValue
                    label={t`Source last success`}
                    reason={t`time not supplied`}
                  />
                )}
              </Alert>
            )}
            {!query.isError && data && <TestAlarmNotice alarms={alarms} />}
            {!query.isError && data && !data.enabled && (
              <Alert
                color='gray'
                icon={<IconPlayerPause />}
                c='var(--mantine-color-text)'
              >
                {recordedTelemetryAsLive(data)
                  ? t`Readings are unavailable for this station. Check station activation and source configuration.`
                  : t`Polling is disabled for this station. Live values are unavailable.`}
              </Alert>
            )}
          </Paper>
          {query.isError ? null : !data ? (
            <Loader />
          ) : (
            <>
              <UnitMimic
                alarmPanel={
                  !knownEmptyAlarms && (
                    <>
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
                            : t`No active alarm details supplied.`}
                        </Text>
                      )}
                    </>
                  )
                }
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
            </>
          )}
        </Stack>
      </MimicMotion>
    </DisplayClockContext>
  );
}
