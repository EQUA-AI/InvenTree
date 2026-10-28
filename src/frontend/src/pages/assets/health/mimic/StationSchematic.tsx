import { t } from '@lingui/core/macro';
import { Alert, Box, Group, Paper, Text, Title } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconInfoCircle } from '@tabler/icons-react';
import {
  type KeyboardEvent,
  type ReactNode,
  useLayoutEffect,
  useRef,
  useState
} from 'react';
import {
  MissingValue,
  ValueTooltip
} from '../../../../components/items/ValueTooltip';
import { unitLabel } from '../../performance/format';
import { recordedTelemetryAsLive } from '../../telemetryPresentation';
import { Timestamp } from '../Timestamp';
import { freshnessText } from './FreshnessLabel';
import { MimicLiveBadge } from './MimicLiveBadge';
import { AlarmAttentionRings, MimicMotionControl } from './MimicMotion';
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
  knownNoActiveAlarms,
  missingContributors,
  operatingState,
  readingSeverity,
  stationRunningSummary
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
  const pumpCount = stationRunningSummary(data);
  const forebayElement = data.layout.elements.find(
    (element) => element.view === 'station' && element.id === 'forebay'
  );
  const forebayPoint = forebayElement
    ? data.station_points[forebayElement.pointer]
    : undefined;
  const forebayRecorded = data.polling_enabled === false;
  const forebayAvailable =
    data.enabled &&
    !data.read_failed &&
    (forebayPoint?.reason == null ||
      (forebayRecorded && forebayPoint.reason === 'stale')) &&
    !failedReading(forebayPoint) &&
    typeof forebayPoint?.value === 'number' &&
    Number.isFinite(forebayPoint.value);
  const forebayValue = forebayAvailable
    ? valueText({ ...forebayPoint, reason: null })
    : t`Unavailable`;
  const forebayRecordDetail = [
    t`Last recorded · polling off`,
    data.source?.name ?? t`Source unavailable`,
    freshnessText(forebayPoint, clock),
    clock.label
  ].join(' · ');
  const forebayLabel = forebayAvailable
    ? `${t`Forebay level`}: ${forebayValue}. ${forebayRecorded ? `${forebayRecordDetail}. ` : ''}${t`Not to scale`}`
    : `${t`Forebay level`}: ${t`Unavailable`}`;
  const forebayUnavailableReason = !data.enabled
    ? t`Polling disabled`
    : data.read_failed || failedReading(forebayPoint)
      ? t`Read failed`
      : reasonLabel(forebayPoint?.reason ?? null) || t`Unavailable`;
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
  const viewport = useRef<HTMLDivElement>(null);
  const desktop = useMediaQuery('(min-width: 48em)');
  const [available, setAvailable] = useState(0);
  useLayoutEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const resize = new ResizeObserver(([entry]) =>
      setAvailable(entry.contentRect.width)
    );
    resize.observe(node);
    node.scrollLeft = desktop || forebayRecorded ? 0 : 24;
    return () => resize.disconnect();
  }, [desktop, bays.length > 0, forebayRecorded]);
  const perRow =
    desktop && available > 0 && available < 1100
      ? Math.ceil(bays.length / 2)
      : bays.length;
  const rows = Math.max(1, Math.ceil(bays.length / Math.max(1, perRow)));
  const start = 154;
  const slot =
    desktop && available > 0
      ? Math.max(
          60,
          Math.min(88, (available - start - 26) / Math.max(1, perRow))
        )
      : 88;
  const width = Math.max(520, start + slot * perRow + 26);
  const bottom = 216;

  return (
    <div className={visual.surface}>
      <div className={classes.header}>
        <Group gap={4} wrap='nowrap'>
          <Title order={4}>{t`Station schematic`}</Title>
          <ValueTooltip
            label={`${data.layout.review_status !== 'approved' ? t`Layout pending plant approval` : t`Schematic layout`} · ${provenance} · ${data.unconfigured_thresholds} · ${t`Station readings without configured limits`}`}
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
          {data.polling_enabled !== false && <MimicLiveBadge data={data} />}
          <MimicMotionControl />
          {actions}
        </Group>
        <Group gap='md' className={classes.observed}>
          {forebayRecorded && (
            <Text size='xs'>{t`Last recorded state · polling off`}</Text>
          )}
          <Timestamp
            value={currentSource ? data.last_observed_at : data.last_poll_at}
            clock={clock}
            compactOnPhone
          />
          {knownNoActiveAlarms(data) && (
            <Text size='xs'>{t`No active alarms`}</Text>
          )}
          <Text size='xs'>{t`${data.unconfigured_thresholds} signals without limits`}</Text>
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
                  data-severity={
                    element.id === 'pumps-running' ||
                    (element.id === 'forebay' &&
                      (!forebayAvailable || forebayRecorded))
                      ? null
                      : readingSeverity(point)
                  }
                  data-reading-value
                >
                  {element.id === 'pumps-running' ? (
                    pumpCount.known > 0 ? (
                      <ValueTooltip
                        label={
                          forebayRecorded
                            ? t`Calculated from the last recorded bay statuses shown in the schematic; the separate station counter is retained in station readings.`
                            : t`Calculated from the same current bay statuses shown in the schematic; the separate station counter is retained in station readings.`
                        }
                      >
                        {pumpCount.unknown > 0
                          ? t`${pumpCount.running} confirmed of ${pumpCount.total}`
                          : t`${pumpCount.running} of ${pumpCount.total}`}
                      </ValueTooltip>
                    ) : (
                      <MissingValue
                        label={label}
                        reason={t`No current pump status is available; the station counter does not identify a running pump.`}
                      />
                    )
                  ) : element.id === 'forebay' && !forebayAvailable ? (
                    <MissingValue
                      label={label}
                      reason={forebayUnavailableReason}
                    />
                  ) : element.id === 'forebay' && forebayRecorded ? (
                    <ValueTooltip label={forebayRecordDetail}>
                      {forebayValue}
                    </ValueTooltip>
                  ) : reading ? (
                    <ValueTooltip label={detail}>{reading}</ValueTooltip>
                  ) : (
                    <UnavailableReading point={point} label={label} />
                  )}
                </Text>
                {element.id === 'pumps-running' && (
                  <Text size='xs'>
                    {forebayRecorded
                      ? t`Last recorded total`
                      : t`Calculated total`}
                  </Text>
                )}
                {element.id === 'forebay' &&
                  forebayAvailable &&
                  forebayRecorded && (
                    <Text size='xs'>{forebayRecordDetail}</Text>
                  )}
              </Paper>
            );
          })}
        {Object.entries(data.totals).map(([name, total]) => {
          const missing = missingContributors(total, bays);
          const label =
            name === 'power'
              ? t`Plant power`
              : name === 'flow'
                ? t`Plant flow`
                : name;
          const detail = [
            total.derived ? t`Calculated total` : t`Source measurement`,
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
                    reason={
                      missing ??
                      (reasonLabel(total.reason) || t`Reason not supplied`)
                    }
                  />
                ) : (
                  <ValueTooltip label={detail}>
                    {numberText(total.value)}
                    {total.unit ? ` ${unitLabel(total.unit)}` : ''}
                  </ValueTooltip>
                )}
              </Text>
              {missing && (
                <Text size='xs' className={classes.totalReason}>
                  {missing}
                </Text>
              )}
            </Paper>
          );
        })}
      </div>
      {(pumpCount.disagreement || pumpCount.unknown > 0) && (
        <Alert
          mt='xs'
          color='orange'
          styles={{ message: { color: 'var(--mantine-color-text)' } }}
        >
          {pumpCount.disagreement && (
            <Text size='sm' data-count-mismatch>
              {t`Count mismatch — station counter reports ${numberText(pumpCount.reported!)}. Check source status readings.`}
            </Text>
          )}
          {pumpCount.unknown > 0 && (
            <Text size='sm'>{t`${pumpCount.unknown} bays without a current status`}</Text>
          )}
        </Alert>
      )}
      {!!bays.length && (
        <Box
          component='section'
          ref={viewport}
          className={classes.viewport}
          tabIndex={0}
          aria-label={t`Station pump bays`}
        >
          <svg
            viewBox={`0 0 ${width} ${320 * rows}`}
            className={classes.drawing}
            style={{ minWidth: width }}
            aria-label={t`Station schematic`}
          >
            <title>{t`Station schematic`}</title>
            <defs>
              <pattern
                id='station-mimic-hatch'
                width='14'
                height='14'
                patternUnits='userSpaceOnUse'
              >
                <rect width='14' height='14' fill='var(--mantine-color-body)' />
                <path
                  d='M-2 2L2 -2M0 14L14 0M12 16L16 12'
                  stroke='light-dark(var(--mantine-color-gray-6), var(--mantine-color-dark-2))'
                  strokeWidth={0.75}
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
              {Array.from({ length: rows }, (_, row) => (
                <g
                  key={row}
                  data-station-row
                  transform={`translate(0 ${row * 320})`}
                >
                  {/* biome-ignore lint/a11y/noInteractiveElementToNoninteractiveRole: SVG group is an unfocusable, atomic forebay illustration. */}
                  <g
                    role='img'
                    aria-label={forebayLabel}
                    focusable='false'
                    data-forebay
                  >
                    <title>{forebayLabel}</title>
                    {forebayAvailable && (
                      <path
                        d='M24 122q8-5 16 0t16 0t16 0t16 0t16 0t16 0V222H24Z'
                        className={classes.forebayWater}
                        data-forebay-water
                      />
                    )}
                    <path
                      d='M18 60H24V222H120V60H126V228H18Z'
                      className={classes.manifold}
                      data-forebay-tank
                    />
                    <text
                      x={72}
                      y={174}
                      textAnchor='middle'
                      className={classes.forebayValue}
                    >
                      {forebayAvailable ? forebayValue : '—'}
                    </text>
                    <text
                      x={72}
                      y={246}
                      textAnchor='middle'
                      className={classes.forebayCaption}
                    >
                      {forebayAvailable
                        ? forebayRecorded
                          ? t`Recorded · unscaled`
                          : t`Not to scale`
                        : t`Unavailable`}
                    </text>
                  </g>
                  <path
                    d={`M126 ${bottom}H${width - 18}v12H126`}
                    className={classes.manifold}
                  />
                  <path
                    d={`M${start - 8} 10H${width - 10}v12H${start - 8}z`}
                    className={classes.manifold}
                  />
                  {bays
                    .slice(row * perRow, (row + 1) * perRow)
                    .map((bay, index) => {
                      const x = start + slot * (index + 0.5);
                      const operation = operatingState(data, bay);
                      const statusElement = data.layout.elements.find(
                        (item) => item.id === 'pump-status'
                      );
                      const lastCode = statusElement
                        ? bay.points[
                            expandPointer(statusElement.pointer, bay.key)
                          ]?.value
                        : null;
                      const retainedState =
                        operation === 'stale' && lastCode != null
                          ? Object.entries(
                              data.layout.status_values ?? {}
                            ).find(([, codes]) =>
                              codes.includes(String(lastCode))
                            )?.[0]
                          : undefined;
                      const glyphState = retainedState ?? operation;

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
                          {!forebayRecorded && hasDischargeFlow(data, bay) && (
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
                          {glyphState === 'fault' && (
                            <g
                              data-fault-icon
                              opacity={operation === 'stale' ? 0.5 : 1}
                              stroke={
                                operation === 'stale'
                                  ? state.line
                                  : 'light-dark(var(--mantine-color-red-9), var(--mantine-color-red-1))'
                              }
                              fill='none'
                              strokeWidth={2}
                            >
                              <path
                                d={`M${x} 93l11 21h-22zM${x} 100v6M${x} 109v1`}
                              />
                            </g>
                          )}
                          {glyphState === 'running' && (
                            <path
                              data-running-icon
                              opacity={operation === 'stale' ? 0.5 : 1}
                              d={`M${x - 5} 95l12 9l-12 9z`}
                              fill={
                                operation === 'stale'
                                  ? state.line
                                  : 'light-dark(var(--mantine-color-green-9), var(--mantine-color-green-1))'
                              }
                              stroke='none'
                            />
                          )}
                          {glyphState === 'idle' && (
                            <path
                              opacity={operation === 'stale' ? 0.5 : 1}
                              d={`M${x - 4} 96v16M${x + 4} 96v16`}
                              stroke={state.line}
                              strokeWidth={3}
                            />
                          )}
                          {alarm && (
                            <g data-alarm-marker={bay.alarm}>
                              {attention && (
                                <AlarmAttentionRings
                                  x={x}
                                  y={44}
                                  radius={13}
                                  color={alarm}
                                  attention={attention}
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
                                    ? 'var(--aimms-warning-ink)'
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
                          {!!bayPower[row * perRow + index].value && (
                            <text
                              x={x}
                              y={290}
                              textAnchor='middle'
                              fontSize={12}
                              fill='currentColor'
                              stroke='none'
                            >
                              {bayPower[row * perRow + index].value}
                            </text>
                          )}
                          {!!bayPower[row * perRow + index].quality && (
                            <text
                              x={x}
                              y={308}
                              textAnchor='middle'
                              fontSize={11}
                              fill='currentColor'
                              stroke='none'
                            >
                              {bayPower[row * perRow + index].quality}
                            </text>
                          )}
                        </g>
                      );
                    })}
                </g>
              ))}
            </g>
          </svg>
        </Box>
      )}
    </div>
  );
}
