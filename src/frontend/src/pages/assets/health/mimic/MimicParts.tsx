import { t } from '@lingui/core/macro';
import { Text } from '@mantine/core';
import { useState } from 'react';
import { ValueTooltip } from '../../../../components/items/ValueTooltip';
import { unitLabel } from '../../performance/format';
import { StatusBadge } from '../StatusBadge';
import { AlarmAttentionRings } from './MimicMotion';
import { failedReading } from './MimicValue';

import { type Display, summarize } from './MimicReadings';
import classes from './UnitMimic.module.css';
import { numberText, reasonLabel, usable } from './format';
import { alarmAttention } from './mimicVisuals';
import type { LayoutPart, MimicAlarm, MimicData, MimicPoint } from './types';

/** Values belong beside a part's name; full sensor names stay in its tooltip. */
export function compactReadings(
  points: MimicPoint[],
  display: Display
): string {
  const groups = new Map<string, number[]>();
  const states: string[] = [];
  for (const point of points.filter(
    (point) => usable(point) && !failedReading(point)
  )) {
    if (typeof point.value !== 'number') {
      states.push(display(point));
      continue;
    }
    const unit = point.unit && point.unit !== '1' ? unitLabel(point.unit) : '';
    const quantity =
      !unit && /(?:VIBRATION|VIB|VELOMETER)/i.test(point.pointer)
        ? t`Vibration`
        : '';
    const key = `${quantity}\u0000${unit}`;
    groups.set(key, [...(groups.get(key) ?? []), point.value]);
  }
  return [
    ...[...groups].map(([key, values]) => {
      const [quantity, unit] = key.split('\u0000');
      const low = Math.min(...values);
      const high = Math.max(...values);
      const value =
        low === high
          ? numberText(low)
          : `${numberText(low)} – ${numberText(high)}`;
      return `${quantity ? `${quantity} ` : ''}${value}${unit ? ` ${unit}` : ''}`;
    }),
    ...new Set(states)
  ].join(' · ');
}

/** What is known of one part of the pump from its readings. */
export type PartState = {
  part: LayoutPart;
  /** The number it carries on the drawing and in the key. */
  number: number;
  /** What this pump's registry calls it; the layout's name if it has none. */
  name: string;
  points: MimicPoint[];
  lines: string[];
  unavailable: number;
  verdict: ReturnType<typeof summarize>['verdict'];
  attention?: 'unacknowledged' | 'acknowledged';
  tests: MimicAlarm[];
};

/**
 * The pump's parts, each with the readings that belong to it.
 *
 * A part is matched by its catalogue code, never by tag name: the three
 * stations spell their tags differently and share nothing else. A part this
 * pump has no readings for is still returned, empty - the drawing shows what a
 * pump of this kind has, and says so where this one reports nothing.
 */
export function partStates(data: MimicData, display: Display): PartState[] {
  return (data.layout.parts ?? []).map((part, index) => {
    const points = Object.values(data.points).filter(
      (point) => point.part_code === part.code
    );
    const summary = summarize(points, display);
    const tests = data.alarms.filter(
      (alarm) =>
        alarm.synthetic &&
        ['open', 'acknowledged'].includes(alarm.anomaly_status ?? '') &&
        points.some((point) => point.pointer === alarm.pointer)
    );
    const testAlarm = tests[0];
    return {
      part,
      number: index + 1,
      name: points.find((point) => point.group)?.group ?? part.label,
      points,
      lines: [
        ...tests.map((alarm) => t`TEST ${display(alarm)}`),
        ...summary.lines
      ],
      tests,
      unavailable: summary.unavailable,
      verdict:
        testAlarm?.severity === 'critical' ? 'critical' : summary.verdict,
      attention: alarmAttention(
        data.alarms,
        undefined,
        points.map((point) => point.pointer)
      )
    };
  });
}

/** The colour a part's verdict is marked in; null when it has none. */
export function verdictColor(state: PartState) {
  if (!state.points.length) return null;
  if (state.verdict === 'critical')
    return 'var(--aimms-critical, light-dark(var(--mantine-color-red-9), var(--mantine-color-red-5)))';
  if (state.verdict === 'warning')
    return 'var(--aimms-warning, light-dark(var(--mantine-color-orange-9), var(--mantine-color-orange-5)))';
  if (state.verdict === 'normal')
    return 'var(--aimms-normal, light-dark(var(--mantine-color-green-9), var(--mantine-color-green-5)))';
  if (state.verdict === 'unknown') return 'var(--mantine-color-body)';
  return 'light-dark(var(--mantine-color-gray-2), var(--mantine-color-dark-4))';
}

/**
 * The colours the drawing's regions take from their parts.
 *
 * A region is outlined in red or amber when its part has a reading past a
 * limit, and in the page's accent while it is pointed at. Nothing is outlined
 * green: a part with no limit to be judged against is not thereby healthy.
 */
export function partOutlines(states: PartState[], active: string | null) {
  const outlines: Record<string, string> = {};
  for (const state of states) {
    const color =
      active === state.part.code
        ? 'var(--aimms-focus, light-dark(var(--mantine-color-blue-8), var(--mantine-color-blue-3)))'
        : state.verdict === 'critical' || state.verdict === 'warning'
          ? verdictColor(state)
          : null;
    if (color) outlines[`--mimic-${state.part.id}`] = color;
  }
  return outlines;
}

/** One consistent number, shared by the callout and the native drawing. */
function PartNumber({
  state,
  x,
  y,
  active = false,
  scale = 1
}: Readonly<{
  state: PartState;
  x: number;
  y: number;
  active?: boolean;
  scale?: number;
}>) {
  const color = verdictColor(state);
  const assessed = ['critical', 'warning', 'normal'].includes(
    state.verdict ?? ''
  );
  return (
    <>
      <circle
        data-marker-backing
        cx={x}
        cy={y}
        r={
          (state.attention ||
          state.verdict === 'warning' ||
          state.verdict === 'critical'
            ? 17
            : 13) * scale
        }
        fill='var(--mantine-color-body)'
        stroke='var(--mantine-color-body)'
        strokeWidth={scale}
      />
      {state.attention && (
        <AlarmAttentionRings
          x={x}
          y={y}
          radius={14}
          scale={scale}
          attention={state.attention}
          color={
            state.verdict === 'warning'
              ? 'var(--aimms-warning)'
              : 'var(--aimms-critical)'
          }
        />
      )}
      {state.verdict === 'critical' || state.verdict === 'warning' ? (
        <polygon
          data-number-surface
          points={
            state.verdict === 'critical'
              ? `${x},${y - 13 * scale} ${x + 13 * scale},${y} ${x},${y + 13 * scale} ${x - 13 * scale},${y}`
              : `${x},${y - 14 * scale} ${x + 14 * scale},${y + 12 * scale} ${x - 14 * scale},${y + 12 * scale}`
          }
          fill={color ?? undefined}
          stroke={active ? 'var(--aimms-focus)' : (color ?? undefined)}
          strokeWidth={(active ? 3 : 1.5) * scale}
        />
      ) : (
        <circle
          data-number-surface
          cx={x}
          cy={y}
          r={12 * scale}
          fill={
            state.verdict === 'normal'
              ? 'var(--mantine-color-body)'
              : (color ?? 'var(--mantine-color-body)')
          }
          stroke={
            active
              ? 'var(--aimms-focus, light-dark(var(--mantine-color-blue-8), var(--mantine-color-blue-3)))'
              : assessed
                ? (color ?? undefined)
                : 'light-dark(var(--mantine-color-gray-7), var(--mantine-color-dark-0))'
          }
          strokeWidth={(active ? 3 : 1.5) * scale}
          strokeDasharray={color || active ? undefined : '3 2'}
        />
      )}
      <text
        x={state.verdict === 'normal' ? x - 3 * scale : x}
        y={y + (state.verdict === 'warning' ? 8 : 5) * scale}
        textAnchor='middle'
        fontSize={(state.verdict === 'normal' ? 13 : 15) * scale}
        fontWeight={700}
        fill={
          assessed
            ? state.verdict === 'warning'
              ? 'var(--aimms-warning-ink)'
              : state.verdict === 'normal'
                ? 'var(--mantine-color-text)'
                : 'light-dark(var(--mantine-color-white), var(--mantine-color-dark-9))'
            : 'var(--mantine-color-text)'
        }
      >
        {state.number}
      </text>
      {state.verdict === 'normal' && (
        <path
          data-normal-check
          d={`M${x + 3 * scale} ${y - 1 * scale}l${2 * scale} ${2 * scale}l${4 * scale} ${-5 * scale}`}
          fill='none'
          stroke={color ?? undefined}
          strokeWidth={1.8 * scale}
          strokeLinecap='round'
          strokeLinejoin='round'
        />
      )}
    </>
  );
}

/** Stand-off annotations leave the engineering geometry unchanged. */
const markerPositions: Record<number, readonly [number, number]> = {
  1: [468, 68],
  2: [702, 160],
  3: [430, 198],
  4: [436, 246],
  5: [480, 280],
  6: [656, 349],
  7: [730, 272],
  8: [594, 390],
  9: [488, 351],
  10: [484, 518],
  11: [646, 395],
  12: [708, 352],
  13: [786, 352]
};

export function markerPosition(state: PartState): readonly [number, number] {
  return markerPositions[state.number] ?? [state.part.x, state.part.y];
}

export function MimicPartMarkers({
  states,
  active,
  scale = 1,
  onPart,
  onActive
}: Readonly<{
  states: PartState[];
  active: string | null;
  scale?: number;
  onPart?: (state: PartState) => void;
  onActive: (code: string | null) => void;
}>) {
  return (
    <g data-part-markers>
      {states.map((state) => (
        // biome-ignore lint/a11y/useKeyWithClickEvents: each marker has the same action as its named, keyboard-accessible HTML callout
        <g
          key={state.part.code}
          data-part={state.part.code}
          data-verdict={state.verdict}
          onClick={() => onPart?.(state)}
          onMouseEnter={() => onActive(state.part.code)}
          onMouseLeave={() => onActive(null)}
          style={{ cursor: 'pointer' }}
        >
          <PartNumber
            state={state}
            x={markerPosition(state)[0]}
            y={markerPosition(state)[1]}
            active={active === state.part.code}
            scale={scale}
          />
        </g>
      ))}
    </g>
  );
}

/** Semantic callouts flow in the page grid instead of sharing SVG coordinates. */
export function MimicParts({
  states,
  active,
  onActive,
  onPart,
  display
}: Readonly<{
  states: PartState[];
  active: string | null;
  onActive: (code: string | null) => void;
  onPart?: (state: PartState) => void;
  display: Display;
}>) {
  const [focused, setFocused] = useState<string | null>(null);
  return (
    <>
      {states.map((state) => {
        const { part } = state;
        const empty = !state.points.length;
        const lit = active === part.code || focused === part.code;
        const missing = empty
          ? t`No readings on this pump`
          : [
              ...new Set(
                state.points
                  .filter((point) => point.value === null || !!point.reason)
                  .map(
                    (point) =>
                      reasonLabel(point.reason) || t`Reason not supplied`
                  )
              )
            ].join(' · ');
        const description = [
          state.name,
          ...state.lines,
          missing,
          state.points.some(
            (point) =>
              !point.unit && /(?:VIBRATION|VIB|VELOMETER)/i.test(point.pointer)
          )
            ? t`Unit not confirmed`
            : null
        ]
          .filter(Boolean)
          .join('\n');
        const summary = compactReadings(state.points, display);
        const failures = state.points.filter(failedReading);
        const retained = compactReadings(
          failures
            .filter((point) => point.reason !== 'not_finite')
            .map((point) => ({ ...point, reason: null, quality: 'good' })),
          display
        );
        return (
          <ValueTooltip
            key={part.code}
            label={description}
            className={classes.callout}
            data-part={part.code}
            data-verdict={state.verdict}
            data-alarm-attention={state.attention}
            data-active={lit || undefined}
            accessibleLabel={[
              `${state.number}. ${state.name}`,
              ...state.tests.map((alarm) => t`TEST ${display(alarm)}`),
              (state.tests.length ? `${t`Measured`}: ` : '') +
                (summary ||
                  retained ||
                  (failures.length ? t`Read failed` : `— · ${missing}`)),
              failures.length
                ? retained
                  ? t`Bad quality`
                  : t`Read failed`
                : '',
              state.verdict === 'critical'
                ? t`Critical`
                : state.verdict === 'warning'
                  ? t`Warning`
                  : ''
            ]
              .filter(Boolean)
              .join(' · ')}
            onClick={() => onPart?.(state)}
            onMouseEnter={() => onActive(part.code)}
            onMouseLeave={() => onActive(focused)}
            onFocus={() => {
              setFocused(part.code);
              onActive(part.code);
            }}
            onBlur={() => {
              setFocused(null);
              onActive(null);
            }}
          >
            <span className={classes.calloutHeading}>
              <svg
                width={28}
                height={28}
                viewBox='-3 -3 34 34'
                aria-hidden='true'
                data-part-number
              >
                <PartNumber state={state} x={14} y={14} />
              </svg>
              <Text
                component='span'
                className={classes.calloutName}
                data-part-label
              >
                {state.name}
              </Text>
            </span>
            <span className={classes.calloutSummary} data-part-summary>
              {state.tests.length > 0 && (
                <Text
                  component='span'
                  fw={700}
                  c='light-dark(var(--mantine-color-red-9), var(--mantine-color-red-3))'
                  data-part-test
                >
                  {state.tests
                    .map((alarm) => t`TEST ${display(alarm)}`)
                    .join(' · ')}
                  <br />
                </Text>
              )}
              <Text
                component='span'
                fw={400}
                c={state.tests.length ? 'var(--mantine-color-text)' : undefined}
                data-part-measured
              >
                {state.tests.length ? `${t`Measured`}: ` : ''}
                {summary ||
                  retained ||
                  (failures.length ? t`Read failed` : '—')}
              </Text>
            </span>
            {!!failures.length && (
              <StatusBadge tone='failed' color='red' size='sm'>
                {retained ? t`Bad quality` : t`Read failed`}
              </StatusBadge>
            )}
            {(state.verdict === 'critical' || state.verdict === 'warning') && (
              <Text component='span' size='xs' fw={600} data-part-verdict>
                {state.verdict === 'critical' ? t`Critical` : t`Warning`}
              </Text>
            )}
          </ValueTooltip>
        );
      })}
    </>
  );
}
