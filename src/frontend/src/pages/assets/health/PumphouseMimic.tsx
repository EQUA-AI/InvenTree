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
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { RefreshAction } from '../../../components/buttons/RefreshAction';
import operations from '../operations.module.css';
import { recordedTelemetryAsLive } from '../telemetryPresentation';
import { MimicMotion } from './mimic/MimicMotion';
import { TestAlarmNotice } from './mimic/TestAlarmNotice';
import {
  DisplayClockContext,
  formatInstant,
  useDisplayClock
} from './mimic/observedTime';

import { AlarmTable } from './mimic/AlarmTable';
import { ReadingSections } from './mimic/MimicReadings';
import { alarmColor, alarmLabel, stateStyle } from './mimic/MimicSchematic';
import { StationSchematic } from './mimic/StationSchematic';
import { PANEL, UnitMimic } from './mimic/UnitMimic';
import { displayFor, stateLabel } from './mimic/format';
import { useStationMimic } from './useStationMimic';

export { reasonLabel, unchangedLabel } from './mimic/format';
export type { MimicData, MimicPoint } from './mimic/types';

/** The states a bay can be drawn in, in the order the legend lists them. */
const LEGEND = ['running', 'idle', 'fault', 'stale', 'unknown'];

/** A stable key keeps state and alarm shapes recognizable as readings change. */
function Legend() {
  return (
    <Group gap='lg' justify='center' mt='xs'>
      {LEGEND.map((state) => {
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
            <Text size='xs'>{alarmLabel(alarm)}</Text>
          </Group>
        );
      })}
    </Group>
  );
}

export default function PumphouseMimic({ stationId }: { stationId: number }) {
  const unitHeading = useRef<HTMLHeadingElement>(null);
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
    <Group gap={6}>
      <RefreshAction
        loading={query.isFetching}
        onClick={() => query.refetch()}
      />
    </Group>
  );
  useEffect(() => {
    if (!unit || data?.selected_unit !== unit) return;
    const heading = unitHeading.current;
    if (!heading) return;
    heading.scrollIntoView({
      block: 'start',
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
        ? 'auto'
        : 'smooth'
    });
    heading.focus({ preventScroll: true });
  }, [unit, data?.selected_unit]);
  const bay = data?.bays.find((b) => b.key === unit);
  const select = (key: string) =>
    setUnit((current) => (current === key ? null : key));

  return (
    <DisplayClockContext value={clock}>
      <MimicMotion>
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
              <TestAlarmNotice alarms={data.alarms} />
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
                {!!data.bays.length && <Legend />}
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
              {unit && data.selected_unit === unit ? (
                <UnitMimic
                  headingRef={unitHeading}
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
              {!!data.alarms.length && (
                <>
                  <Title order={4}>{t`Threshold alarms`}</Title>
                  <AlarmTable
                    alarms={data.alarms}
                    units={Object.fromEntries(
                      data.bays
                        .filter((bay) => bay.machine != null)
                        .map((bay) => [bay.machine!, bay.key])
                    )}
                    display={display}
                    onAcknowledged={() => query.refetch()}
                  />
                </>
              )}
            </>
          )}
        </Stack>
      </MimicMotion>
    </DisplayClockContext>
  );
}
