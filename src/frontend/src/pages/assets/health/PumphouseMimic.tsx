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
import { useState } from 'react';
import { Link } from 'react-router-dom';
import operations from '../operations.module.css';
import { recordedTelemetryAsLive } from '../telemetryPresentation';
import {
  DisplayClockContext,
  formatInstant,
  useDisplayClock
} from './mimic/observedTime';

import { AlarmTable } from './mimic/AlarmTable';
import { ReadingSections } from './mimic/MimicReadings';
import { alarmColor, alarmLabel, stateStyle } from './mimic/MimicSchematic';
import { StationSchematic } from './mimic/StationSchematic';
import classes from './mimic/StationSchematic.module.css';
import { PANEL, UnitMimic } from './mimic/UnitMimic';
import { displayFor, stateLabel } from './mimic/format';
import { operatingState } from './mimic/mimicVisuals';
import type { MimicData } from './mimic/types';
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
    const operation = operatingState(data, bay);
    const key = LEGEND.includes(operation) ? operation : 'unknown';
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
            <Text size='xs'>{stateLabel(state)}</Text>
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
                clipPath:
                  alarm === 'critical'
                    ? 'polygon(50% 0, 100% 50%, 50% 100%, 0 50%)'
                    : 'polygon(50% 0, 100% 100%, 0 100%)',
                background: alarmColor(alarm) ?? undefined
              }}
            />
            <Text size='xs' fw={600}>
              {alarmLabel(alarm)}
            </Text>
          </Group>
        );
      })}
    </Group>
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
  const represented = new Set(
    data?.layout.elements
      .filter((element) => element.view === 'station')
      .map((element) => element.pointer)
  );
  const stationPoints = Object.values(data?.station_points ?? {}).filter(
    (point) => !represented.has(point.pointer)
  );
  const provenance = data
    ? `${t`Source`}: ${data.source?.name ?? t`Source not supplied`} · ${currentSource ? t`Last reading` : t`Last poll`}: ${formatInstant(currentSource ? data.last_observed_at : data.last_poll_at, clock) ?? t`Reading time not supplied`} · ${clock.label}`
    : '';
  const refresh = (
    <Button
      className={classes.refresh}
      variant='default'
      size='xs'
      loading={query.isFetching}
      onClick={() => query.refetch()}
    >
      {t`Refresh`}
    </Button>
  );
  const bay = data?.bays.find((b) => b.key === unit);
  const select = (key: string) =>
    setUnit((current) => (current === key ? null : key));

  return (
    <DisplayClockContext value={clock}>
      <Stack ref={ref} gap='sm' mih={160}>
        {query.isError ? (
          <Stack>
            {refresh}
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
            <Paper
              withBorder
              radius='md'
              p='sm'
              bg={PANEL}
              className={operations.mimicPanel}
            >
              <StationSchematic
                data={data}
                selected={unit}
                onSelect={select}
                provenance={provenance}
                actions={refresh}
              />
              {!!data.bays.length && <Legend data={data} />}
            </Paper>
            {!data.bays.length && (
              <Alert>{t`No pump slots are registered for this station.`}</Alert>
            )}
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
            {!!data.alarms.length && (
              <>
                <Title order={4}>{t`Threshold alarms`}</Title>
                <AlarmTable
                  alarms={data.alarms}
                  display={display}
                  onAcknowledged={() => query.refetch()}
                />
              </>
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
          </>
        )}
      </Stack>
    </DisplayClockContext>
  );
}
