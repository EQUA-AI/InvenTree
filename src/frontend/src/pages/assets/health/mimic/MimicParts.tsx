import { t } from '@lingui/core/macro';
import { Text, Tooltip, UnstyledButton } from '@mantine/core';
import { useId, useState } from 'react';

import { type Display, summarize } from './MimicReadings';
import classes from './UnitMimic.module.css';
import { reasonLabel, usable } from './format';
import type { LayoutPart, MimicData, MimicPoint } from './types';

/** One bar of a part's strip: a sensor, its height its reading. */
type Bar = {
  point: MimicPoint;
  /** 0 at the part's lowest reading, 1 at its highest; null if unavailable. */
  level: number | null;
};

/**
 * A part's sensors as a row of bars, one each, in the part's main unit.
 *
 * Each bar's height is its reading against the others of the same part, so a
 * sensor reading unlike its neighbours stands out before anyone has read a
 * number - a hot winding among cool ones, a bearing pad warmer than the rest.
 * Only readings in the unit the part reports most of are compared; a shaft
 * speed is not set against winding temperatures. A sensor that cannot be
 * read is a hollow stub, in its place.
 */
export function sensorBars(points: MimicPoint[]): Bar[] {
  const counts = new Map<string, number>();
  for (const point of points) {
    if (typeof point.value === 'number' || !usable(point)) {
      counts.set(point.unit, (counts.get(point.unit) ?? 0) + 1);
    }
  }
  const [unit] = [...counts].sort((a, b) => b[1] - a[1])[0] ?? [];
  if (unit === undefined) return [];
  const family = points.filter((point) => point.unit === unit);
  const values = family
    .filter(usable)
    .map((point) => point.value)
    .filter((value): value is number => typeof value === 'number');
  if (values.length < 2) return [];
  const [low, high] = [Math.min(...values), Math.max(...values)];
  return family.map((point) => ({
    point,
    level:
      usable(point) && typeof point.value === 'number'
        ? high === low
          ? 0.5
          : (point.value - low) / (high - low)
        : null
  }));
}

function barColor(point: MimicPoint) {
  if (!point.thresholds_configured) return 'var(--mimic-bar-neutral)';
  if (point.condition === 'critical') return 'var(--mantine-color-red-filled)';
  if (point.condition === 'warning') return 'var(--mantine-color-orange-9)';
  if (point.condition === 'normal') return 'var(--mantine-color-green-9)';
  return 'var(--mimic-bar-neutral)';
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
  if (state.verdict === 'critical') return 'var(--mantine-color-red-9)';
  if (state.verdict === 'warning') return 'var(--mantine-color-orange-9)';
  if (state.verdict === 'normal') return 'var(--mantine-color-green-9)';
  return 'var(--mantine-color-gray-9)';
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
        ? 'var(--mantine-primary-color-filled)'
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
  return (
    <>
      <circle
        cx={x}
        cy={y}
        r={12 * scale}
        fill={color ?? 'var(--mantine-color-body)'}
        stroke={
          active
            ? 'var(--mantine-primary-color-filled)'
            : color
              ? 'var(--mantine-color-body)'
              : 'var(--mantine-color-text)'
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
          color
            ? state.verdict === 'warning' || state.verdict === 'normal'
              ? 'var(--mantine-color-black)'
              : 'var(--mantine-color-white)'
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
  const id = useId();
  return (
    <>
      {states.map((state) => {
        const { part } = state;
        const empty = !state.points.length;
        const bars = sensorBars(state.points);
        const lit = active === part.code || focused === part.code;
        const missing = empty
          ? t`No readings on this pump`
          : [
              ...new Set(
                state.points
                  .filter((point) => point.value === null || !!point.reason)
                  .map((point) => reasonLabel(point.reason ?? 'no_data'))
              )
            ].join(' · ');
        const description = [state.name, ...state.lines, missing]
          .filter(Boolean)
          .join('\n');
        const descriptionId = `${id}-${state.number}`;
        const summary = state.lines.slice(0, 2);
        return (
          <Tooltip
            key={part.code}
            label={description}
            multiline
            maw={360}
            events={{ hover: true, focus: true, touch: true }}
          >
            <UnstyledButton
              className={classes.callout}
              data-part={part.code}
              data-verdict={state.verdict}
              data-active={lit || undefined}
              aria-label={`${state.number}. ${state.name}`}
              aria-describedby={descriptionId}
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
                {summary.length ? (
                  summary.join(' · ')
                ) : (
                  <span aria-label={missing}>—</span>
                )}
              </span>
              <span id={descriptionId} className={classes.srOnly}>
                {description}
              </span>
              {!!bars.length && (
                <svg
                  className={classes.sensorBars}
                  width='100%'
                  height={13}
                  viewBox={`0 0 ${Math.max(1, bars.length * 12)} 13`}
                  preserveAspectRatio='xMinYMid meet'
                  aria-hidden='true'
                >
                  {bars.map((bar, slot) => {
                    const height = bar.level === null ? 3 : 3 + bar.level * 9;
                    return (
                      <rect
                        key={bar.point.pointer}
                        data-bar={bar.point.pointer}
                        x={slot * 12}
                        y={13 - height}
                        width={10}
                        height={height}
                        rx={1}
                        fill={bar.level === null ? 'none' : barColor(bar.point)}
                        stroke={
                          bar.level === null
                            ? 'var(--mantine-color-text)'
                            : 'none'
                        }
                        strokeWidth={1}
                        strokeDasharray={bar.level === null ? '2 1' : undefined}
                      >
                        <title>
                          {bar.point.label}: {display(bar.point)}
                        </title>
                      </rect>
                    );
                  })}
                </svg>
              )}
            </UnstyledButton>
          </Tooltip>
        );
      })}
    </>
  );
}
