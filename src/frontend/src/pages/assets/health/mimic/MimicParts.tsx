import { t } from '@lingui/core/macro';
import { type KeyboardEvent, useState } from 'react';

import { type Display, summarize } from './MimicReadings';
import { usable } from './format';
import type { LayoutPart, MimicData, MimicPoint } from './types';

/** The size of a part's entry in the key, in the drawing's own units. */
const ENTRY = { width: 220, height: 70, pitch: 77, top: 20 };

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
  if (!point.thresholds_configured) return 'var(--mantine-color-gray-5)';
  if (point.condition === 'critical') return 'var(--mantine-color-red-filled)';
  if (point.condition === 'warning')
    return 'var(--mantine-color-yellow-filled)';
  if (point.condition === 'normal') return 'var(--mantine-color-green-filled)';
  return 'var(--mantine-color-gray-5)';
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
function verdictColor(state: PartState) {
  if (!state.points.length) return null;
  if (state.verdict === 'critical') return 'var(--mantine-color-red-filled)';
  if (state.verdict === 'warning') return 'var(--mantine-color-yellow-filled)';
  if (state.verdict === 'normal') return 'var(--mantine-color-green-filled)';
  return 'var(--mantine-color-gray-filled)';
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

function clip(text: string, length: number) {
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

/**
 * The pump's parts, numbered on the drawing and listed down its two sides.
 *
 * Thirteen parts with a box and a leader each would bury the drawing they
 * describe, so a part is a number on the drawing and an entry in the key with
 * the same number. The entry says what the part's readings amount to; pointing
 * at either lights the region, and choosing either opens the part's readings.
 */
export function MimicParts({
  states,
  width,
  active,
  onActive,
  onPart,
  display
}: Readonly<{
  states: PartState[];
  /** Width of the drawing, to stand the second column against its edge. */
  width: number;
  active: string | null;
  onActive: (code: string | null) => void;
  onPart?: (state: PartState) => void;
  /** How a bar's reading is written in its tooltip. */
  display: Display;
}>) {
  // The keyboard's place, kept apart from the pointer's so that tabbing away
  // from an entry does not leave a region lit.
  const [focused, setFocused] = useState<string | null>(null);
  const rows = Math.ceil(states.length / 2);

  return (
    <>
      {states.map((state, index) => {
        const { part } = state;
        const column = index < rows ? 0 : 1;
        const x = column === 0 ? 16 : width - 16 - ENTRY.width;
        const y = ENTRY.top + (index - column * rows) * ENTRY.pitch;
        const empty = !state.points.length;
        const color = verdictColor(state);
        const bars = sensorBars(state.points);
        const lit = active === part.code || focused === part.code;
        const unavailable = state.unavailable;
        const missing = unavailable ? t`${unavailable} unavailable` : '';
        const first = state.lines[0] ?? missing;
        const second = state.lines[1] ?? (state.lines[0] ? missing : '');
        const press = (event: KeyboardEvent) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onPart?.(state);
          }
        };
        const marker = (cx: number, cy: number, r: number) => (
          <>
            <circle
              cx={cx}
              cy={cy}
              r={r}
              fill={color ?? 'var(--mantine-color-body)'}
              stroke={
                lit
                  ? 'var(--mantine-color-gray-filled)'
                  : color
                    ? 'var(--mantine-color-body)'
                    : 'var(--mantine-color-dimmed)'
              }
              strokeWidth={lit ? 2.5 : 1.5}
              strokeDasharray={color || lit ? undefined : '3 2'}
            />
            <text
              x={cx}
              y={cy + 3.6}
              textAnchor='middle'
              fontSize={10.5}
              fontWeight={700}
              fill={
                color
                  ? 'var(--mantine-color-white)'
                  : 'var(--mantine-color-dimmed)'
              }
            >
              {state.number}
            </text>
          </>
        );

        return (
          <g
            key={part.id}
            data-part={part.code}
            onMouseEnter={() => onActive(part.code)}
            onMouseLeave={() => onActive(null)}
            style={{ cursor: empty ? 'default' : 'pointer' }}
          >
            <g
              // biome-ignore lint/a11y/useSemanticElements: an SVG has no button element to draw with; this is one in role, focus and keys
              role='button'
              tabIndex={0}
              aria-label={`${state.number}. ${state.name}`}
              onClick={() => onPart?.(state)}
              onKeyDown={press}
              onFocus={() => setFocused(part.code)}
              onBlur={() => setFocused(null)}
              style={{ outline: 'none' }}
            >
              <title>
                {[state.name, ...state.lines].join('\n') || state.name}
              </title>
              <rect
                x={x}
                y={y}
                width={ENTRY.width}
                height={ENTRY.height}
                rx={6}
                fill={
                  lit
                    ? 'var(--mantine-color-gray-light)'
                    : 'var(--mantine-color-body)'
                }
                stroke={
                  lit
                    ? 'var(--mantine-color-gray-filled)'
                    : 'var(--mantine-color-default-border)'
                }
                strokeWidth={1.2}
                strokeDasharray={empty && !lit ? '4 3' : undefined}
              />
              {marker(x + 17, y + 18, 9.5)}
              <text
                x={x + 33}
                y={y + 22}
                fontSize={11.5}
                fontWeight={600}
                fill={
                  empty
                    ? 'var(--mantine-color-dimmed)'
                    : 'var(--mantine-color-text)'
                }
              >
                {clip(state.name, 30)}
              </text>
              <text
                x={x + 12}
                y={y + 38}
                fontSize={11}
                fontStyle={empty ? 'italic' : undefined}
                fill='var(--mantine-color-dimmed)'
              >
                {empty ? t`No readings on this pump` : clip(first, 36)}
              </text>
              <text
                x={x + 12}
                y={y + 51}
                fontSize={10}
                fill='var(--mantine-color-dimmed)'
              >
                {empty ? '' : clip(second, 40)}
              </text>
              {bars.map((bar, slot) => {
                const gap = 2;
                const width = Math.min(
                  10,
                  (ENTRY.width - 24 - gap * (bars.length - 1)) / bars.length
                );
                const height = bar.level === null ? 3 : 3 + bar.level * 9;
                return (
                  <rect
                    key={bar.point.pointer}
                    data-bar={bar.point.pointer}
                    x={x + 12 + slot * (width + gap)}
                    y={y + ENTRY.height - 5 - height}
                    width={width}
                    height={height}
                    rx={1}
                    fill={bar.level === null ? 'none' : barColor(bar.point)}
                    stroke={
                      bar.level === null
                        ? 'var(--mantine-color-dimmed)'
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
            </g>
            {/* The same number, on the part itself. */}
            {/* biome-ignore lint/a11y/useKeyWithClickEvents: the entry above is this part's button and takes the keys; this is the same part under the pointer */}
            <g aria-hidden onClick={() => onPart?.(state)}>
              {marker(part.x, part.y, 10)}
            </g>
          </g>
        );
      })}
    </>
  );
}
