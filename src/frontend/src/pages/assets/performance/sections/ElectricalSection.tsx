import { t } from '@lingui/core/macro';
import { Stack, useComputedColorScheme, useMantineTheme } from '@mantine/core';
import { useMemo } from 'react';

import { RelationChart } from '../RelationChart';
import { familyColors } from '../TrendChart';
import { reactiveSharesPowerAxis } from '../evaluation';
import { withRole } from '../resolve';
import { relationPoints } from '../series';
import {
  CardGrid,
  ChartCard,
  ParameterTrend,
  type SectionProps,
  lineFor
} from './common';

/**
 * Electrical performance: what the machine draws and how cleanly.
 *
 * Power and power factor share a chart because they are read together; current
 * and voltage share one for the same reason. Relationships against speed and
 * pressure are drawn as patterns, not explanations.
 *
 * Power factor is dimensionless and has the right axis to itself. Reactive
 * power joins active power on the left axis only when their units carry the
 * same magnitude (MW with Mvar, kW with kvar); otherwise it is drawn on its
 * own trend beside them. It never shares a scale with the power factor.
 */
export function ElectricalSection({
  parameters,
  series,
  window,
  syncId,
  onZoom
}: Readonly<SectionProps>) {
  const theme = useMantineTheme();
  const scheme = useComputedColorScheme('light');
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

  const sharedAxis = reactiveSharesPowerAxis(
    power.map((p) => p.signal.unit),
    reactive.map((p) => p.signal.unit)
  );
  // Reactive power with no active power to share with: it takes the left
  // axis of its own card and the power factor keeps the right.
  const ownReactiveCard = reactive.length > 0 && !sharedAxis;
  const powerCard = sharedAxis ? [...power, ...reactive] : power;
  const hasPower = powerCard.length + factor.length > 0;
  const hasSupply = currents.length + voltages.length > 0;
  const hasExcitation = excitationCurrent.length + excitationVoltage.length > 0;

  if (!hasPower && !hasSupply && !hasExcitation && frequency.length === 0) {
    return null;
  }

  const currentColors = familyColors(currents.length, ['blue'], theme, scheme);
  const voltageColors = familyColors(voltages.length, ['teal'], theme, scheme);

  return (
    <Stack gap='md'>
      <CardGrid>
        {hasPower && (
          <ChartCard
            title={t`Power and power factor`}
            description={
              sharedAxis
                ? factor.length > 0
                  ? t`Active and reactive power on the left axis (${power[0].signal.unit} / ${reactive[0].signal.unit}); power factor on the right.`
                  : t`Active and reactive power share the left axis (${power[0].signal.unit} / ${reactive[0].signal.unit}).`
                : power.length > 0 && factor.length > 0
                  ? t`Active power on the left axis; power factor on the right.`
                  : undefined
            }
          >
            <ParameterTrend
              parameters={[...powerCard, ...factor]}
              lines={[
                ...power.map((p) => lineFor(p, 'blue.7')),
                ...(sharedAxis
                  ? reactive.map((p) => lineFor(p, 'violet.6'))
                  : []),
                ...factor.map((p) => lineFor(p, 'orange.6', 'right'))
              ]}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={onZoom}
              unit={
                sharedAxis
                  ? `${power[0].signal.unit} / ${reactive[0].signal.unit}`
                  : power[0]?.signal.unit
              }
              // Dimensionless: named by its series rather than a unit.
              rightUnit={factor[0]?.signal.unit || undefined}
            />
          </ChartCard>
        )}
        {ownReactiveCard && (
          <ChartCard
            title={t`Reactive power`}
            description={
              power.length > 0
                ? t`Drawn apart from active power: their units (${power[0].signal.unit || t`none`} and ${reactive[0].signal.unit || t`none`}) are not stated at the same magnitude.`
                : undefined
            }
          >
            <ParameterTrend
              parameters={reactive}
              lines={reactive.map((p) => lineFor(p, 'violet.6'))}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={onZoom}
              unit={reactive[0].signal.unit}
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
