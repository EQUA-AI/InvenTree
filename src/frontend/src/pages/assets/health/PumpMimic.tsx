import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
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
  const alarms = (data?.alarms ?? []).filter((a) => a.machine === bay?.machine);
  return (
    <DisplayClockContext value={clock}>
      <Stack ref={ref} gap='sm' mih={160}>
        <Paper
          component='section'
          aria-labelledby='schematic-title'
          className={operations.band}
        >
          <Group justify='space-between' gap='xs'>
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
            <Group gap='sm'>
              {controls}
              <Anchor
                className={operations.link}
                component={Link}
                to={`/machines/machine/${stationId}/mimic`}
                size='sm'
              >{t`Open station mimic`}</Anchor>
              <Button
                variant='default'
                size='xs'
                loading={query.isFetching}
                onClick={() => query.refetch()}
              >{t`Refresh`}</Button>
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
            <Title order={5}>{t`Threshold alarms`}</Title>
            {alarms.length ? (
              <AlarmTable
                alarms={alarms}
                display={displayFor(data, unit)}
                onAcknowledged={() => query.refetch()}
              />
            ) : (
              <Text>{t`No active alarms from configured thresholds.`}</Text>
            )}
          </>
        )}
      </Stack>
    </DisplayClockContext>
  );
}
