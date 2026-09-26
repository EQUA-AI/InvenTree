import { t } from '@lingui/core/macro';
import { Box, SimpleGrid, Stack } from '@mantine/core';
import { Children, type ReactNode, useMemo } from 'react';

import { RelationChart } from '../RelationChart';
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
 * The section's two-column grid, where an odd last card fills the row.
 *
 * Which cards exist depends on the tags a machine carries, so an odd count is
 * normal; without this the final chart is squeezed into half a page of blank.
 * At base the grid is one column and the span is already a no-op.
 */
function CardGrid({ children }: Readonly<{ children: ReactNode }>) {
  // toArray drops the `false` that an absent card's guard leaves behind.
  const cards = Children.toArray(children);
  const spanLast = cards.length % 2 === 1;
  return (
    <SimpleGrid cols={{ base: 1, lg: 2 }} spacing='md'>
      {cards.map((card, index) =>
        spanLast && index === cards.length - 1 ? (
          <Box key={index} style={{ gridColumn: '1 / -1' }}>
            {card}
          </Box>
        ) : (
          card
        )
      )}
    </SimpleGrid>
  );
}

/**
 * Electrical performance: what the machine draws and how cleanly.
 *
 * Power and power factor share a chart because they are read together; current
 * and voltage share one for the same reason. Relationships against speed and
 * pressure are drawn as patterns, not explanations.
 */
export function ElectricalSection({
  parameters,
  series,
  window,
  syncId,
  onZoom
}: Readonly<SectionProps>) {
  const power = withRole(parameters, 'active_power');
  const reactive = withRole(parameters, 'reactive_power');
  const factor = withRole(parameters, 'power_factor');
  const currents = withRole(parameters, 'current', 'phase_current');
  const voltages = withRole(parameters, 'voltage', 'phase_voltage');
  const frequency = withRole(parameters, 'frequency');
  const excitationCurrent = withRole(parameters, 'excitation_current');
  const excitationVoltage = withRole(parameters, 'excitation_voltage');
  const speed = withRole(parameters, 'speed');

  const powerVsSpeed = useMemo(
    () =>
      power[0] && speed[0]
        ? relationPoints(
            series.get(speed[0].signal.binding_id),
            series.get(power[0].signal.binding_id)
          )
        : [],
    [power, speed, series]
  );
  const currentVsPower = useMemo(
    () =>
      power[0] && currents[0]
        ? relationPoints(
            series.get(power[0].signal.binding_id),
            series.get(currents[0].signal.binding_id)
          )
        : [],
    [power, currents, series]
  );

  const hasPower = power.length + reactive.length + factor.length > 0;
  const hasSupply = currents.length + voltages.length > 0;
  const hasExcitation = excitationCurrent.length + excitationVoltage.length > 0;

  if (!hasPower && !hasSupply && !hasExcitation && frequency.length === 0) {
    return null;
  }

  const currentColors = familyColors(currents.length, ['blue']);
  const voltageColors = familyColors(voltages.length, ['teal']);

  return (
    <Stack gap='md'>
      <CardGrid>
        {hasPower && (
          <ChartCard
            title={t`Power and power factor`}
            description={
              power.length === 0 || reactive.length + factor.length === 0
                ? undefined
                : reactive.length > 0
                  ? t`Active power on the left axis; reactive power and power factor on the right.`
                  : t`Active power on the left axis; power factor on the right.`
            }
          >
            <ParameterTrend
              parameters={[...power, ...reactive, ...factor]}
              lines={[
                ...power.map((p) => lineFor(p, 'blue.7')),
                ...reactive.map((p) => lineFor(p, 'violet.6', 'right')),
                ...factor.map((p) => lineFor(p, 'orange.6', 'right'))
              ]}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={onZoom}
              unit={power[0]?.signal.unit}
              rightUnit={factor[0] ? '' : reactive[0]?.signal.unit}
            />
          </ChartCard>
        )}
        {hasSupply && (
          <ChartCard
            title={t`Current and voltage`}
            description={
              currents.length > 0 && voltages.length > 0
                ? t`Current on the left axis, voltage on the right.`
                : undefined
            }
          >
            <ParameterTrend
              parameters={[...currents, ...voltages]}
              lines={[
                ...currents.map((p, i) => lineFor(p, currentColors[i])),
                ...voltages.map((p, i) => lineFor(p, voltageColors[i], 'right'))
              ]}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={onZoom}
              unit={currents[0]?.signal.unit}
              rightUnit={voltages[0]?.signal.unit}
            />
          </ChartCard>
        )}
        {hasExcitation && (
          <ChartCard
            title={t`Excitation`}
            description={
              excitationCurrent.length > 0 && excitationVoltage.length > 0
                ? t`Field current on the left axis, field voltage on the right.`
                : undefined
            }
          >
            <ParameterTrend
              parameters={[...excitationCurrent, ...excitationVoltage]}
              lines={[
                ...excitationCurrent.map((p) => lineFor(p, 'blue.6')),
                ...excitationVoltage.map((p) => lineFor(p, 'teal.6', 'right'))
              ]}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={onZoom}
              unit={excitationCurrent[0]?.signal.unit}
              rightUnit={excitationVoltage[0]?.signal.unit}
            />
          </ChartCard>
        )}
        {frequency.length > 0 && (
          <ChartCard title={t`Frequency`}>
            <ParameterTrend
              parameters={frequency}
              lines={frequency.map((p) => lineFor(p, 'gray.7'))}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={onZoom}
              unit={frequency[0].signal.unit}
            />
          </ChartCard>
        )}
      </CardGrid>

      {(powerVsSpeed.length > 0 || currentVsPower.length > 0) && (
        <CardGrid>
          {powerVsSpeed.length > 0 && power[0] && speed[0] && (
            <RelationChart
              title={t`Relationship: active power and shaft speed`}
              points={powerVsSpeed}
              xLabel={speed[0].label}
              yLabel={power[0].label}
              xUnit={speed[0].signal.unit}
              yUnit={power[0].signal.unit}
              xDecimals={speed[0].definition.decimals}
              yDecimals={power[0].definition.decimals}
              windowSeconds={window.seconds}
            />
          )}
          {currentVsPower.length > 0 && power[0] && currents[0] && (
            <RelationChart
              title={t`Relationship: motor current and active power`}
              points={currentVsPower}
              xLabel={power[0].label}
              yLabel={currents[0].label}
              xUnit={power[0].signal.unit}
              yUnit={currents[0].signal.unit}
              xDecimals={power[0].definition.decimals}
              yDecimals={currents[0].definition.decimals}
              windowSeconds={window.seconds}
            />
          )}
        </CardGrid>
      )}
    </Stack>
  );
}

export default ElectricalSection;
