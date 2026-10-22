import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Group,
  Loader,
  Paper,
  Stack,
  Text,
  Title,
  Tooltip,
  UnstyledButton
} from '@mantine/core';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import operations from '../operations.module.css';
import { recordedTelemetryAsLive } from '../telemetryPresentation';
import { formatInstant, useDisplayClock } from './mimic/observedTime';

import { AlarmTable } from './mimic/AlarmTable';
import { ReadingSections } from './mimic/MimicReadings';
import { alarmColor, alarmLabel, stateStyle } from './mimic/MimicSchematic';
import { MissingValue } from './mimic/MissingValue';
import { StationSchematic } from './mimic/StationSchematic';
import classes from './mimic/StationSchematic.module.css';
import { PANEL, UnitMimic } from './mimic/UnitMimic';
import {
  displayFor,
  numberText,
  reasonLabel,
  stateLabel
} from './mimic/format';
import type { MimicData, Total } from './mimic/types';
import { useStationMimic } from './useStationMimic';

export { reasonLabel, unchangedLabel } from './mimic/format';
export type { MimicData, MimicPoint } from './mimic/types';

/** The states a bay can be drawn in, in the order the legend lists them. */
const LEGEND = ['running', 'idle', 'fault', 'stale', 'unknown'];

/**
 * How many bays are in each state, as a key to the drawing above it.
 *
 * Counts what is drawn, nothing more: a bay whose status cannot be read is
 * counted as unknown, not as idle, and the source's own count of running
 * pumps is a separate reading on the drawing that this does not replace.
 */
function Legend({ data }: Readonly<{ data: MimicData }>) {
  const counts: Record<string, number> = {};
  for (const bay of data.bays) {
    const key = LEGEND.includes(bay.state) ? bay.state : 'unknown';
    counts[key] = (counts[key] ?? 0) + 1;
  }
  const shown = LEGEND.filter(
    (state) => counts[state] || state === 'running' || state === 'idle'
  );

  return (
    <Group gap='lg' justify='center' mt='xs'>
      {shown.map((state) => {
        const style = stateStyle(state);
        return (
          <Group key={state} gap={6} wrap='nowrap' data-legend={state}>
            <Box
              w={12}
              h={12}
              style={{
                borderRadius: '50%',
                background: style.fill,
                border: `1.6px ${style.dashed ? 'dashed' : 'solid'} ${style.line}`
              }}
            />
            <Text size='xs'>
              {stateLabel(state)} {counts[state] ?? 0}
            </Text>
          </Group>
        );
      })}
      {/* Alarms are counted beside the states, not among them: a bay is
          running or idle, and has an alarm or has not. */}
      {(['critical', 'warning'] as const).map((alarm) => {
        const count = data.bays.filter((bay) => bay.alarm === alarm).length;
        if (!count) return null;
        return (
          <Group
            key={alarm}
            gap={6}
            wrap='nowrap'
            data-legend={`alarm-${alarm}`}
          >
            <Box
              w={12}
              h={12}
              style={{
                borderRadius: '50%',
                background: alarmColor(alarm) ?? undefined
              }}
            />
            <Text size='xs' fw={600}>
              {alarmLabel(alarm)} {count}
            </Text>
          </Group>
        );
      })}
    </Group>
  );
}

function TotalCard({ name, total }: Readonly<{ name: string; total: Total }>) {
  const label =
    name === 'power' ? t`Plant power` : name === 'flow' ? t`Plant flow` : name;
  return (
    <Paper
      withBorder
      p='sm'
      radius='sm'
      className={classes.total}
      data-total={name}
    >
      <Tooltip
        label={total.derived ? t`Derived total` : t`Source measurement`}
        events={{ hover: true, focus: true, touch: true }}
      >
        <UnstyledButton className={classes.origin} w='fit-content'>
          <Text component='span' size='xs' fw={600}>
            {label}
          </Text>
        </UnstyledButton>
      </Tooltip>
      <Text
        component='div'
        size='xl'
        fw={700}
        lh={1.3}
        className={classes.totalValue}
      >
        {total.value === null ? (
          <MissingValue
            label={label}
            reason={reasonLabel(total.reason) || t`Not reported by the plant`}
          />
        ) : (
          <>
            {numberText(total.value)}{' '}
            <Text span size='sm' fw={500}>
              {total.unit}
            </Text>
          </>
        )}
      </Text>
    </Paper>
  );
}

export default function PumphouseMimic({ stationId }: { stationId: number }) {
  const [unit, setUnit] = useState<string | null>(null);
  const { ref, query } = useStationMimic(stationId, unit);
  const data = query.data;
  const clock = useDisplayClock(data?.plant_timezone);
  const currentSource =
    recordedTelemetryAsLive(data) && data?.reporting != null;
  const display = displayFor(data, unit);
  const stationPoints = Object.values(data?.station_points ?? {});
  const bay = data?.bays.find((b) => b.key === unit);
  const select = (key: string) =>
    setUnit((current) => (current === key ? null : key));

  return (
    <Stack ref={ref} gap='md' mih={160}>
      <Group justify='space-between' align='center'>
        <Group gap='lg'>
          {data && !query.isError && (
            <>
              <Text size='xs' className={operations.meta}>
                {t`Source`}:{' '}
                {data.source?.name ?? (
                  <MissingValue reason={t`Source not supplied`} />
                )}
              </Text>
              <Text size='xs' className={operations.meta}>
                {currentSource ? t`Last reading` : t`Last poll`}:{' '}
                {formatInstant(
                  currentSource ? data.last_observed_at : data.last_poll_at,
                  clock
                ) ?? (
                  <MissingValue reason={t`Reading time not supplied`} />
                )}{' '}
                · {clock.label}
              </Text>
              {currentSource &&
                data.enabled &&
                data.reporting &&
                !data.last_error_code && (
                  <Badge color='blue' variant='outline'>{t`Live`}</Badge>
                )}
            </>
          )}
        </Group>
        <Button
          className={classes.refresh}
          variant='default'
          size='xs'
          loading={query.isFetching}
          onClick={() => query.refetch()}
        >{t`Refresh`}</Button>
      </Group>
      {query.isError ? (
        <Stack>
          <Alert color='red'>{t`Live readings are unavailable. Refresh to retry; cached values are hidden.`}</Alert>
          {unit && (
            <Button
              variant='default'
              onClick={() => setUnit(null)}
            >{t`Overview only`}</Button>
          )}
        </Stack>
      ) : !data ? (
        <Loader />
      ) : (
        <>
          {!data.enabled && (
            <Alert color='yellow'>
              {recordedTelemetryAsLive(data)
                ? t`Readings are unavailable for this station. Check station activation and source configuration.`
                : t`Polling is disabled for this station. Live values are unavailable.`}
            </Alert>
          )}
          {data.last_error_code && (
            <Alert color='red'>
              {t`Last station polling error`}: {data.last_error_code}
            </Alert>
          )}
          <Paper withBorder radius='md' p='sm' bg={PANEL}>
            <StationSchematic data={data} selected={unit} onSelect={select} />
            {!!data.bays.length && <Legend data={data} />}
          </Paper>
          {!data.bays.length && (
            <Alert>{t`No pump slots are registered for this station.`}</Alert>
          )}
          <div className={classes.totals}>
            {Object.entries(data.totals).map(([name, total]) => (
              <TotalCard key={name} name={name} total={total} />
            ))}
          </div>
          {!!stationPoints.length && (
            <ReadingSections
              display={display}
              sections={[
                {
                  key: 'station',
                  title: t`Station readings`,
                  points: stationPoints
                }
              ]}
            />
          )}
          {unit && data.selected_unit === unit ? (
            <UnitMimic
              key={unit}
              data={data}
              unit={unit}
              actions={
                <>
                  {bay?.machine && (
                    <Anchor
                      component={Link}
                      to={`/machines/machine/${bay.machine}/performance`}
                      size='sm'
                    >
                      {t`Open pump page`}
                    </Anchor>
                  )}
                  <Button
                    variant='subtle'
                    onClick={() => setUnit(null)}
                  >{t`Overview only`}</Button>
                </>
              }
            />
          ) : null}
          <Title order={4}>{t`Threshold alarms`}</Title>
          {data.alarms.length ? (
            <AlarmTable
              alarms={data.alarms}
              display={display}
              onAcknowledged={() => query.refetch()}
            />
          ) : (
            <Text>{t`No active alarms from configured thresholds.`}</Text>
          )}
        </>
      )}
    </Stack>
  );
}
