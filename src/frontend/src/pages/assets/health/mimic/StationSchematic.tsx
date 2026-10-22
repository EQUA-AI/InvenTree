import { t } from '@lingui/core/macro';
import { Box, Group, Paper, Text, Title, VisuallyHidden } from '@mantine/core';
import { IconArrowsHorizontal, IconInfoCircle } from '@tabler/icons-react';
import type { KeyboardEvent, ReactNode } from 'react';
import {
  MissingValue,
  ValueTooltip
} from '../../../../components/items/ValueTooltip';
import { unitLabel } from '../../performance/format';
import { recordedTelemetryAsLive } from '../../telemetryPresentation';
import { Timestamp } from '../Timestamp';
import { StatusBadge as Badge } from '../common';
import { MimicAlarmCoverage } from './MimicAlarmCoverage';
import { alarmColor, alarmLabel, stateStyle } from './MimicSchematic';
import { UnavailableReading, failedReading } from './MimicValue';
import classes from './StationSchematic.module.css';
import {
  byBayNumber,
  elementLabel,
  expandPointer,
  numberText,
  reasonLabel,
  stateLabel,
  statusText,
  usable,
  valueText
} from './format';
import {
  alarmAttention,
  hasDischargeFlow,
  operatingState,
  readingSeverity
} from './mimicVisuals';
import visual from './mimicVisuals.module.css';
import { formatInstant, useDisplayClock } from './observedTime';
import type { MimicData } from './types';

/**
 * Readings live in a grid above the drawing, never on its pipes or pump bays.
 * A narrow viewport scrolls the drawing instead of shrinking its labels.
 */
export function StationSchematic({
  data,
  selected,
  onSelect,
  provenance,
  actions
}: Readonly<{
  data: MimicData;
  selected: string | null;
  onSelect: (key: string) => void;
  provenance: string;
  actions?: ReactNode;
}>) {
  const clock = useDisplayClock();
  const currentSource = recordedTelemetryAsLive(data) && data.reporting != null;
  const observed = formatInstant(
    currentSource ? data.last_observed_at : data.last_poll_at,
    clock
  );
  const bays = [...data.bays].sort(byBayNumber);
  const power = data.layout.elements.find(
    (element) => element.view === 'unit' && element.id === 'pump-power'
  );
  const bayPower = bays.map((bay) => {
    const point = power
      ? bay.points[expandPointer(power.pointer, bay.key)]
      : undefined;
    const failed = failedReading(point);
    const retained =
      failed &&
      point?.value != null &&
      point.reason !== 'not_finite' &&
      (typeof point.value !== 'number' || Number.isFinite(point.value));
    return {
      value:
        retained && point
          ? valueText({ ...point, reason: null })
          : usable(point) &&
              !failed &&
              (bay.state === 'running' || point.value !== 0)
            ? valueText(point)
            : '',
      quality: failed ? (retained ? t`Bad quality` : t`Read failed`) : ''
    };
  });
  // Stable bay geometry; labels are not estimated from character counts.
  const slot = 88;
  const start = 154;
  const width = Math.max(520, start + slot * bays.length + 26);
  const bottom = 216;

  return (
    <div className={visual.surface}>
      <div className={classes.header}>
        <Group gap={4} wrap='nowrap'>
          <Title order={4}>{t`Station schematic`}</Title>
          <ValueTooltip
            label={`${data.layout.review_status !== 'approved' ? t`Reference schematic · physical arrangement unverified. Awaiting plant review.` : t`Reference schematic · physical arrangement unverified`} · ${provenance}`}
            className={classes.info}
            accessibleLabel={
              data.layout.review_status !== 'approved'
                ? t`Schematic awaiting plant review`
                : t`Schematic information`
            }
          >
            <IconInfoCircle size={18} />
          </ValueTooltip>
        </Group>
        <Group gap='xs' wrap='nowrap'>
          {currentSource &&
            data.enabled &&
            data.reporting &&
            !data.last_error_code && (
              <Badge color='blue' variant='outline'>{t`Live`}</Badge>
            )}
          {actions}
        </Group>
        <Group gap='md' className={classes.observed}>
          <Timestamp
            value={currentSource ? data.last_observed_at : data.last_poll_at}
            clock={clock}
            compactOnPhone
          />
          <MimicAlarmCoverage
            station
            count={data.unconfigured_thresholds}
            knownEmpty={
              data.alarm === null &&
              data.bays.every((bay) => bay.alarm === null) &&
              !data.alarms.some(
                (alarm) =>
                  alarm.anomaly !== null &&
                  ['open', 'acknowledged'].includes(alarm.anomaly_status ?? '')
              )
            }
          />
        </Group>
      </div>
      <div className={classes.metrics} data-station-metrics>
        {data.layout.elements
          .filter((element) => element.view === 'station')
          .map((element) => {
            const point = data.station_points[element.pointer];
            const label = elementLabel(element);
            const pointTime = formatInstant(point?.observed_at, clock);
            const reading =
              usable(point) && !failedReading(point)
                ? element.role === 'status'
                  ? statusText(point, data.layout)
                  : valueText(point)
                : null;
            const detail = [
              element.pointer,
              reading && usable(point) && reading !== valueText(point)
                ? `${t`Source code`}: ${valueText(point)}`
                : null,
              pointTime,
              clock.label
            ]
              .filter(Boolean)
              .join(' · ');
            return (
              <Paper
                component='section'
                key={element.id}
                withBorder
                radius='sm'
                px='sm'
                py='xs'
                className={classes.metric}
                data-point={element.pointer}
                data-station-metric={element.id}
                aria-label={label}
              >
                <Text size='xs' className={classes.label}>
                  {label}
                </Text>
                <Text
                  component='div'
                  size='xl'
                  fw={650}
                  className={`${classes.reading} ${visual.value}`}
                  data-severity={readingSeverity(point)}
                  data-reading-value
                >
                  {reading ? (
                    <ValueTooltip label={detail}>{reading}</ValueTooltip>
                  ) : (
                    <UnavailableReading point={point} label={label} />
                  )}
                </Text>
              </Paper>
            );
          })}
        {Object.entries(data.totals).map(([name, total]) => {
          const label =
            name === 'power'
              ? t`Plant power`
              : name === 'flow'
                ? t`Plant flow`
                : name;
          const detail = [
            total.derived ? t`Derived total` : t`Source measurement`,
            total.reason && reasonLabel(total.reason),
            ...total.contributors,
            observed,
            clock.label
          ]
            .filter(Boolean)
            .join(' · ');
          return (
            <Paper
              key={name}
              withBorder
              radius='sm'
              px='sm'
              py='xs'
              className={classes.metric}
              data-total={name}
            >
              <Text size='xs' className={classes.label}>
                {label}
                {total.derived && (
                  <>
                    {' '}
                    <span aria-hidden='true'>Σ</span>
                    <VisuallyHidden>{t`Calculated total`}</VisuallyHidden>
                  </>
                )}
              </Text>
              <Text
                component='div'
                size='xl'
                fw={650}
                className={classes.reading}
                data-reading-value
              >
                {total.value === null ? (
                  <MissingValue
                    label={label}
                    reason={reasonLabel(total.reason) || t`Reason not supplied`}
                  />
                ) : (
                  <ValueTooltip label={detail}>
                    {numberText(total.value)}
                    {total.unit ? ` ${unitLabel(total.unit)}` : ''}
                  </ValueTooltip>
                )}
              </Text>
            </Paper>
          );
        })}
      </div>
      {!!bays.length && (
        <>
          <Text
            component='div'
            size='xs'
            hiddenFrom='sm'
            className={classes.scrollCue}
          >
            <IconArrowsHorizontal size={15} aria-hidden='true' />
            {t`Swipe to view all pump bays`}
          </Text>
          <Box
            component='section'
            className={classes.viewport}
            tabIndex={0}
            aria-label={t`Station pump bays`}
          >
            <svg
              viewBox={`0 0 ${width} 320`}
              className={classes.drawing}
              style={{ minWidth: width }}
              aria-label={t`Station schematic`}
            >
              <title>{t`Station schematic`}</title>
              <defs>
                <pattern
                  id='station-mimic-hatch'
                  width='8'
                  height='8'
                  patternUnits='userSpaceOnUse'
                >
                  <rect width='8' height='8' fill='var(--mantine-color-body)' />
                  <path
                    d='M-2 2L2 -2M0 8L8 0M6 10L10 6'
                    stroke='currentColor'
                    strokeWidth={1.5}
                  />
                </pattern>
              </defs>
              <g
                fill='none'
                stroke='currentColor'
                strokeWidth={2}
                strokeLinecap='round'
                strokeLinejoin='round'
              >
                <path
                  d='M18 60H24V222H120V60H126V228H18Z'
                  className={classes.manifold}
                  data-forebay-tank
                />
                <path
                  d={`M126 ${bottom}H${width - 18}v12H126`}
                  className={classes.manifold}
                />
                <path
                  d={`M${start - 8} 10H${width - 10}v12H${start - 8}z`}
                  className={classes.manifold}
                />
                {bays.map((bay, index) => {
                  const x = start + slot * (index + 0.5);
                  const operation = operatingState(data, bay);
                  const state = stateStyle(operation);
                  const alarm = alarmColor(bay.alarm);
                  const attention =
                    bay.machine == null
                      ? undefined
                      : alarmAttention(data.alarms, bay.machine);
                  const chosen = bay.key === selected;
                  const press = (event: KeyboardEvent) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      onSelect(bay.key);
                    }
                  };
                  return (
                    <g
                      key={bay.key}
                      // biome-ignore lint/a11y/useSemanticElements: SVG equipment selection needs SVG geometry and keyboard semantics
                      role='button'
                      tabIndex={0}
                      aria-pressed={chosen}
                      aria-label={`${bay.key}: ${stateLabel(operation)}${alarm ? `, ${alarmLabel(bay.alarm)}` : ''}`}
                      onClick={() => onSelect(bay.key)}
                      onKeyDown={press}
                      onFocus={(event) =>
                        event.currentTarget.scrollIntoView({
                          block: 'nearest',
                          inline: 'nearest'
                        })
                      }
                      className={classes.bay}
                      data-bay={bay.key}
                      data-operating-state={operation}
                      data-alarm={bay.alarm ?? undefined}
                    >
                      <title>
                        {`${bay.name}: ${stateLabel(operation)}${bay.active ? '' : ` · ${t`Inactive equipment`}`}${alarm ? ` · ${alarmLabel(bay.alarm)}` : ''}`}
                      </title>
                      <rect
                        className={classes.focus}
                        x={x - slot / 2 + 3}
                        y={48}
                        width={slot - 6}
                        height={268}
                        rx={6}
                        style={
                          chosen
                            ? {
                                fill: 'var(--mantine-primary-color-light)',
                                stroke:
                                  'var(--aimms-focus, light-dark(var(--mantine-color-blue-8), var(--mantine-color-blue-3)))'
                              }
                            : undefined
                        }
                      />
                      <path
                        d={`M${x} 196V${bottom}M${x + 20} 176h6V22`}
                        className={classes.pipeEdge}
                      />
                      <path
                        d={`M${x} 196V${bottom}M${x + 20} 176h6V22`}
                        className={classes.pipeCore}
                      />
                      {hasDischargeFlow(data, bay) && (
                        <path
                          d={`M${x + 20} 176h6V22`}
                          className={visual.flow}
                          data-discharge-flow
                        />
                      )}
                      <path d={`M${x} 144V156`} stroke={state.line} />
                      <rect
                        x={x - 15}
                        y={66}
                        width={30}
                        height={78}
                        rx={4}
                        fill={
                          operation === 'stale'
                            ? 'url(#station-mimic-hatch)'
                            : state.fill
                        }
                        stroke={state.line}
                        strokeDasharray={state.dashed ? '5 4' : undefined}
                        data-state-body
                      />
                      <circle
                        cx={x}
                        cy={176}
                        r={20}
                        fill={
                          operation === 'stale'
                            ? 'url(#station-mimic-hatch)'
                            : state.fill
                        }
                        stroke={state.line}
                        strokeDasharray={state.dashed ? '5 4' : undefined}
                        data-state-body
                      />
                      <circle
                        cx={x}
                        cy={176}
                        r={3}
                        fill={state.line}
                        stroke='none'
                        data-impeller
                      />
                      <path
                        d={`M${x} 173q8-1 10-10M${x + 3} 178q-2 8 8 10M${x - 3} 178q-7-5-13 2`}
                        stroke={state.line}
                        strokeWidth={1.5}
                      />
                      {operation === 'fault' && (
                        <g
                          data-fault-icon
                          stroke='light-dark(var(--mantine-color-red-9), var(--mantine-color-red-1))'
                          fill='none'
                          strokeWidth={2}
                        >
                          <path
                            d={`M${x} 93l11 21h-22zM${x} 100v6M${x} 109v1`}
                          />
                        </g>
                      )}
                      {operation === 'running' && (
                        <path
                          data-running-icon
                          d={`M${x - 5} 95l12 9l-12 9z`}
                          fill='light-dark(var(--mantine-color-green-9), var(--mantine-color-green-1))'
                          stroke='none'
                        />
                      )}
                      {operation === 'idle' && (
                        <path
                          d={`M${x - 4} 96v16M${x + 4} 96v16`}
                          stroke={state.line}
                          strokeWidth={3}
                        />
                      )}
                      {alarm && (
                        <g data-alarm-marker={bay.alarm}>
                          {attention && (
                            <circle
                              cx={x}
                              cy={44}
                              r={13}
                              stroke={alarm}
                              strokeWidth={2}
                              className={visual.attention}
                              data-attention={attention}
                            />
                          )}
                          <polygon
                            points={
                              bay.alarm === 'critical'
                                ? `${x},33 ${x + 11},44 ${x},55 ${x - 11},44`
                                : `${x},32 ${x + 12},55 ${x - 12},55`
                            }
                            fill={alarm}
                            stroke={alarm}
                            strokeWidth={2}
                          />
                          <text
                            x={x}
                            y={48}
                            textAnchor='middle'
                            fontSize={14}
                            fontWeight={700}
                            fill={
                              bay.alarm === 'warning'
                                ? 'light-dark(var(--mantine-color-black), var(--mantine-color-dark-9))'
                                : 'light-dark(var(--mantine-color-white), var(--mantine-color-dark-9))'
                            }
                            stroke='none'
                          >
                            !
                          </text>
                        </g>
                      )}
                      <text
                        x={x}
                        y={250}
                        textAnchor='middle'
                        fontSize={14}
                        fontWeight={650}
                        fill='currentColor'
                        stroke='none'
                      >
                        {bay.key}
                      </text>
                      <text
                        x={x}
                        y={270}
                        textAnchor='middle'
                        fontSize={13}
                        fill='currentColor'
                        stroke='none'
                      >
                        {stateLabel(operation)}
                      </text>
                      {!!bayPower[index].value && (
                        <text
                          x={x}
                          y={290}
                          textAnchor='middle'
                          fontSize={12}
                          fill='currentColor'
                          stroke='none'
                        >
                          {bayPower[index].value}
                        </text>
                      )}
                      {!!bayPower[index].quality && (
                        <text
                          x={x}
                          y={308}
                          textAnchor='middle'
                          fontSize={11}
                          fill='currentColor'
                          stroke='none'
                        >
                          {bayPower[index].quality}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            </svg>
          </Box>
        </>
      )}
    </div>
  );
}
