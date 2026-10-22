import { t } from '@lingui/core/macro';
import { Text } from '@mantine/core';
import { useState } from 'react';
import { ValueTooltip } from '../../../../components/items/ValueTooltip';
import { unitLabel } from '../../performance/format';
import { StatusBadge } from '../StatusBadge';
import { failedReading } from './MimicValue';

import { type Display, summarize } from './MimicReadings';
import classes from './UnitMimic.module.css';
import { numberText, reasonLabel, usable } from './format';
import type { LayoutPart, MimicData, MimicPoint } from './types';

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
    const unit = point.unit ? unitLabel(point.unit) : '';
    groups.set(unit, [...(groups.get(unit) ?? []), point.value]);
  }
  return [
    ...[...groups].map(([unit, values]) => {
      const low = Math.min(...values);
      const high = Math.max(...values);
      const value =
        low === high
          ? numberText(low)
          : `${numberText(low)} – ${numberText(high)}`;
      return `${value}${unit ? ` ${unit}` : ''}`;
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
    return {
      part,
      number: index + 1,
      name: points.find((point) => point.group)?.group ?? part.label,
      points,
      lines: summary.lines,
      unavailable: summary.unavailable,
      verdict: summary.verdict
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
        cx={x}
        cy={y}
        r={12 * scale}
        fill={color ?? 'var(--mantine-color-body)'}
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
      <text
        x={x}
        y={y + 5 * scale}
        textAnchor='middle'
        fontSize={15 * scale}
        fontWeight={700}
        fill={
          assessed
            ? state.verdict === 'warning' || state.verdict === 'normal'
              ? 'light-dark(var(--mantine-color-black), var(--mantine-color-dark-9))'
              : 'light-dark(var(--mantine-color-white), var(--mantine-color-dark-9))'
            : 'var(--mantine-color-text)'
        }
      >
        {state.number}
      </text>
    </>
  );
}

/** Native coordinates are unchanged; HTML callouts provide the hit targets. */
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
            x={state.part.x}
            y={state.part.y}
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
  /** Retained for the legacy station renderer's component contract. */
  width?: number;
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
        const description = [state.name, ...state.lines, missing]
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
            data-active={lit || undefined}
            accessibleLabel={`${state.number}. ${state.name}`}
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
                width={26}
                height={26}
                viewBox='0 0 28 28'
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
              {summary || retained || (failures.length ? t`Read failed` : '—')}
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
