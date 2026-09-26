import { t } from '@lingui/core/macro';
import { SimpleGrid, Stack, Text } from '@mantine/core';
import { useMemo } from 'react';

import { RelationChart } from '../RelationChart';
import { SensorGroupCard } from '../SensorGroupCard';
import { familyColors } from '../TrendChart';
import { withRole } from '../resolve';
import { relationPoints } from '../series';
import {
  ChartCard,
  ParameterTrend,
  type SectionProps,
  lineFor
} from './common';

/**
 * Speed, operating state and the hydraulic side of the machine.
 *
 * The two are drawn together because they are read together: a pump's speed
 * means little without its status, and its pressure means little without its
 * valve positions and the forebay it draws from. The relationship views put
 * pressure against speed, power and valve position as operating patterns.
 */
export function OperationSection({
  parameters,
  series,
  window,
  syncId,
  onZoom
}: Readonly<SectionProps>) {
  const speed = withRole(parameters, 'speed');
  const status = withRole(parameters, 'status');
  const motorStatus = withRole(parameters, 'motor_status');
  const valves = withRole(parameters, 'valve');
  const pressure = withRole(parameters, 'pressure');
  const flow = withRole(parameters, 'flow');
  const forebay = withRole(parameters, 'forebay');
  const surge = withRole(parameters, 'surge_pool');
  const power = withRole(parameters, 'active_power');
  const others = [
    { role: 'draft_tube', title: t`Draft tube`, hue: 'cyan' },
    { role: 'spiral_casing', title: t`Spiral casing`, hue: 'cyan' },
    { role: 'delivery_line', title: t`Delivery line`, hue: 'cyan' }
  ];

  // Share of the window the status trace reports the bay running. Sampled
  // windows make this an estimate, and it is labelled as one.
  const running = useMemo(() => {
    const entry = status[0]
      ? series.get(status[0].signal.binding_id)
      : undefined;
    if (!entry?.available) return null;
    const known = entry.samples.filter((s) => s.v !== null);
    if (known.length === 0) return null;
    const on = known.filter((s) => (s.v as number) > 0).length;
    return { share: on / known.length, samples: known.length };
  }, [status, series]);

  const speedVsPressure = useMemo(
    () =>
      speed[0] && pressure[0]
        ? relationPoints(
            series.get(speed[0].signal.binding_id),
            series.get(pressure[0].signal.binding_id)
          )
        : [],
    [speed, pressure, series]
  );
  const powerVsPressure = useMemo(
    () =>
      power[0] && pressure[0]
        ? relationPoints(
            series.get(power[0].signal.binding_id),
            series.get(pressure[0].signal.binding_id)
          )
        : [],
    [power, pressure, series]
  );
  const valveVsPressure = useMemo(
    () =>
      valves[0] && pressure[0]
        ? relationPoints(
            series.get(valves[0].signal.binding_id),
            series.get(pressure[0].signal.binding_id)
          )
        : [],
    [valves, pressure, series]
  );

  const anything =
    speed.length +
      status.length +
      valves.length +
      pressure.length +
      flow.length +
      forebay.length >
      0 || others.some((o) => withRole(parameters, o.role).length > 0);
  if (!anything) {
    return null;
  }

  const valveColors = familyColors(valves.length, ['teal']);

  return (
    <Stack gap='md'>
      <SimpleGrid cols={{ base: 1, lg: 2 }} spacing='md'>
        {(speed.length > 0 || status.length > 0) && (
          <ChartCard
            title={t`Shaft speed and status`}
            description={
              status.length > 0
                ? t`Speed on the left axis; the plant's run/stop status on the right, drawn as steps (1 running, 0 idle).`
                : undefined
            }
          >
            <ParameterTrend
              parameters={[...speed, ...status, ...motorStatus]}
              lines={[
                ...speed.map((p) => lineFor(p, 'blue.7')),
                ...status.map((p) => lineFor(p, 'green.7', 'right', true)),
                ...motorStatus.map((p, i) =>
                  lineFor(p, i === 0 ? 'gray.6' : 'gray.4', 'right', true)
                )
              ]}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={onZoom}
              unit={speed[0]?.signal.unit}
              rightUnit=''
            />
            {running && (
              <Text size='xs' c='dimmed'>
                {t`Reported running for ${Math.round(running.share * 100)}% of the window (${running.samples} status readings${window.resolutionSeconds > 10 ? t`, sampled` : ''}).`}
              </Text>
            )}
          </ChartCard>
        )}
        {(pressure.length > 0 || valves.length > 0) && (
          <ChartCard
            title={t`Discharge pressure and valve position`}
            description={
              valves.length > 0
                ? t`Pressure on the left axis, valve positions on the right.`
                : undefined
            }
          >
            <ParameterTrend
              parameters={[...pressure, ...valves]}
              lines={[
                ...pressure.map((p) => lineFor(p, 'indigo.7')),
                ...valves.map((p, i) => lineFor(p, valveColors[i], 'right'))
              ]}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={onZoom}
              unit={pressure[0]?.signal.unit}
              rightUnit={valves[0]?.signal.unit}
            />
          </ChartCard>
        )}
        {(forebay.length > 0 || flow.length > 0 || surge.length > 0) && (
          <ChartCard
            title={t`Forebay level and discharge flow`}
            description={
              forebay.length > 0 && flow.length > 0
                ? t`Level on the left axis, flow on the right.`
                : undefined
            }
          >
            <ParameterTrend
              parameters={[...forebay, ...surge, ...flow]}
              lines={[
                ...forebay.map((p) => lineFor(p, 'cyan.7')),
                ...surge.map((p) => lineFor(p, 'cyan.4')),
                ...flow.map((p) =>
                  lineFor(
                    p,
                    'blue.6',
                    forebay.length + surge.length > 0 ? 'right' : 'left'
                  )
                )
              ]}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={onZoom}
              unit={(forebay[0] ?? surge[0] ?? flow[0])?.signal.unit}
              rightUnit={
                forebay.length + surge.length > 0
                  ? flow[0]?.signal.unit
                  : undefined
              }
            />
          </ChartCard>
        )}
        {others.map((o) => {
          const group = withRole(parameters, o.role);
          return group.length > 0 ? (
            <SensorGroupCard
              key={o.role}
              title={o.title}
              parameters={group}
              series={series}
              window={window}
              syncId={syncId}
              hue={o.hue}
              onZoom={onZoom}
            />
          ) : null;
        })}
      </SimpleGrid>

      {(speedVsPressure.length > 0 ||
        powerVsPressure.length > 0 ||
        valveVsPressure.length > 0) && (
        <SimpleGrid cols={{ base: 1, lg: 3 }} spacing='md'>
          {speedVsPressure.length > 0 && speed[0] && pressure[0] && (
            <RelationChart
              title={t`Relationship: speed and discharge pressure`}
              points={speedVsPressure}
              xLabel={speed[0].label}
              yLabel={pressure[0].label}
              xUnit={speed[0].signal.unit}
              yUnit={pressure[0].signal.unit}
              xDecimals={speed[0].definition.decimals}
              yDecimals={pressure[0].definition.decimals}
              windowSeconds={window.seconds}
            />
          )}
          {powerVsPressure.length > 0 && power[0] && pressure[0] && (
            <RelationChart
              title={t`Relationship: active power and discharge pressure`}
              points={powerVsPressure}
              xLabel={power[0].label}
              yLabel={pressure[0].label}
              xUnit={power[0].signal.unit}
              yUnit={pressure[0].signal.unit}
              xDecimals={power[0].definition.decimals}
              yDecimals={pressure[0].definition.decimals}
              windowSeconds={window.seconds}
            />
          )}
          {valveVsPressure.length > 0 && valves[0] && pressure[0] && (
            <RelationChart
              title={t`Relationship: valve position and discharge pressure`}
              points={valveVsPressure}
              xLabel={valves[0].label}
              yLabel={pressure[0].label}
              xUnit={valves[0].signal.unit}
              yUnit={pressure[0].signal.unit}
              xDecimals={valves[0].definition.decimals}
              yDecimals={pressure[0].definition.decimals}
              windowSeconds={window.seconds}
            />
          )}
        </SimpleGrid>
      )}
    </Stack>
  );
}

export default OperationSection;
