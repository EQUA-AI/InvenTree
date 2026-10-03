import { t } from '@lingui/core/macro';
import { type CSSProperties, type KeyboardEvent, useState } from 'react';

import unitDrawing from '../../../../assets/mimic/pump-unit.svg?raw';
import stationDrawing from '../../../../assets/mimic/pumphouse-overview.svg?raw';
import {
  MimicParts,
  type PartState,
  partOutlines,
  partStates
} from './MimicParts';
import type { Display } from './MimicReadings';
import {
  byBayNumber,
  elementLabel,
  expandPointer,
  reasonLabel,
  stateLabel,
  statusText,
  usable,
  valueText
} from './format';
import type {
  Bay,
  BayArea,
  LayoutElement,
  MimicData,
  MimicPoint
} from './types';

/** The size of a reading's tag, in the drawing's own units. */
const TAG = { width: 150, height: 40 };

/** Where the checked-in station drawing leaves room for bays. */
const DEFAULT_BAYS: BayArea = { x: 240, y: 70, width: 590, height: 134 };

/**
 * How each state is drawn.
 *
 * Idle is grey, not amber: a stopped pump is not a warning. Amber is kept for
 * a state that is itself in doubt - a status too old to trust - and a state
 * nobody can read at all is drawn empty and dashed.
 */
const STATE: Record<string, { fill: string; line: string; dashed?: boolean }> =
  {
    running: {
      fill: 'var(--mantine-color-green-light)',
      line: 'var(--mantine-color-green-filled)'
    },
    idle: {
      fill: 'var(--mantine-color-body)',
      line: 'var(--mantine-color-gray-filled)'
    },
    fault: {
      fill: 'var(--mantine-color-red-light)',
      line: 'var(--mantine-color-red-filled)'
    },
    stale: {
      fill: 'var(--mantine-color-yellow-light)',
      line: 'var(--mantine-color-yellow-filled)',
      dashed: true
    }
  };
const UNKNOWN = {
  fill: 'transparent',
  line: 'var(--mantine-color-dimmed)',
  dashed: true
};

export function stateStyle(state: string) {
  return STATE[state] ?? UNKNOWN;
}

/** The drawings name their colours; this is what the page gives them. */
const THEME = {
  '--mimic-line': 'var(--mantine-color-text)',
  '--mimic-muted': 'var(--mantine-color-dimmed)',
  '--mimic-surface': 'var(--mantine-color-body)',
  '--mimic-structure': 'var(--mantine-color-default-border)',
  '--mimic-water': 'var(--mantine-color-blue-light)'
} as CSSProperties;

function viewBoxOf(drawing: string) {
  return /viewBox="([^"]+)"/.exec(drawing)?.[1] ?? '0 0 600 300';
}

function conditionLine(point?: MimicPoint) {
  if (!usable(point) || !point.thresholds_configured) return null;
  if (point.condition === 'critical') return 'var(--mantine-color-red-filled)';
  if (point.condition === 'warning') {
    return 'var(--mantine-color-yellow-filled)';
  }
  if (point.condition === 'normal') return 'var(--mantine-color-green-filled)';
  return null;
}

/**
 * One reading, tagged onto the drawing where the layout places it.
 *
 * A reading that cannot be shown says why in the place its value would be,
 * dashed and dimmed, rather than a blank or a zero. The tag's tooltip carries
 * the same in full.
 */
function Tag({
  element,
  pointer,
  point,
  data
}: Readonly<{
  element: LayoutElement;
  pointer: string;
  point?: MimicPoint;
  data: MimicData;
}>) {
  const { x, y } = element;
  const shown = usable(point);
  const text =
    element.role === 'status'
      ? statusText(point, data.layout)
      : valueText(point);
  // Only a *missing* point is unbound; a present one states its own reason.
  const reason = point ? reasonLabel(point.reason) : reasonLabel('not_bound');
  const accent = conditionLine(point);
  const hasTarget = element.ax !== undefined && element.ay !== undefined;
  const from = hasTarget && {
    x: Math.min(Math.max(element.ax as number, x), x + TAG.width),
    y: Math.min(Math.max(element.ay as number, y), y + TAG.height)
  };

  return (
    <g data-point={pointer}>
      <title>
        {elementLabel(element)}: {text} {reason}
      </title>
      {from && (
        <>
          <line
            x1={from.x}
            y1={from.y}
            x2={element.ax}
            y2={element.ay}
            stroke='var(--mantine-color-dimmed)'
            strokeWidth={1}
          />
          <circle
            cx={element.ax}
            cy={element.ay}
            r={2.5}
            fill='var(--mantine-color-dimmed)'
          />
        </>
      )}
      <rect
        x={x}
        y={y}
        width={TAG.width}
        height={TAG.height}
        rx={6}
        fill='var(--mantine-color-body)'
        stroke={accent ?? 'var(--mantine-color-default-border)'}
        strokeWidth={accent ? 2 : 1.2}
        strokeDasharray={shown ? undefined : '4 3'}
      />
      <text
        x={x + 10}
        y={y + 15}
        fontSize={11}
        fill='var(--mantine-color-dimmed)'
      >
        {elementLabel(element)}
      </text>
      <text
        x={x + 10}
        y={y + 32}
        fontSize={shown ? 15 : 13}
        fontWeight={shown ? 600 : 400}
        fontStyle={shown ? undefined : 'italic'}
        fill={
          shown ? 'var(--mantine-color-text)' : 'var(--mantine-color-dimmed)'
        }
      >
        {shown ? text : reason || text}
      </text>
    </g>
  );
}

/**
 * The station's bays, drawn between its two headers.
 *
 * One per registered slot, in number order, each the colour of its state and
 * each a button: the drawing is also how a bay is chosen. They are spread over
 * the width the layout allows, so a station of four and one of fourteen use
 * the same drawing; the glyphs shrink before they overlap, and their labels go
 * before they collide.
 */
function Bays({
  data,
  selected,
  onSelect
}: Readonly<{
  data: MimicData;
  selected?: string | null;
  onSelect?: (key: string) => void;
}>) {
  // The bay under the pointer or holding the keyboard focus. Drawn here
  // because a shape inside an SVG gets no focus ring of its own.
  const [active, setActive] = useState<string | null>(null);
  const area = data.layout.bays ?? DEFAULT_BAYS;
  const bays = [...data.bays].sort(byBayNumber);
  if (!bays.length) return null;

  const slot = area.width / bays.length;
  const top = area.y;
  const bottom = area.y + area.height;
  const radius = Math.max(4, Math.min(16, slot * 0.3));
  const pumpY = bottom - 36;
  const motor = {
    width: radius * 1.6,
    top: top + 34,
    bottom: pumpY - radius - 8
  };
  const drawn = data.layout.elements.filter((e) => e.view === 'unit');
  const power = drawn.find((e) => e.id === 'pump-power');

  return (
    <>
      {bays.map((bay: Bay, index) => {
        const centre = area.x + slot * (index + 0.5);
        const style = stateStyle(bay.state);
        const chosen = selected === bay.key;
        const flowing = bay.state === 'running';
        const reading = power
          ? bay.points[expandPointer(power.pointer, bay.key)]
          : undefined;
        const press = (event: KeyboardEvent) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onSelect?.(bay.key);
          }
        };

        return (
          <g
            key={bay.key}
            // biome-ignore lint/a11y/useSemanticElements: an SVG has no button element to draw with; this is one in role, focus and keys
            role='button'
            tabIndex={0}
            aria-pressed={chosen}
            aria-label={`${bay.key}: ${stateLabel(bay.state)}`}
            onClick={() => onSelect?.(bay.key)}
            onKeyDown={press}
            onFocus={() => setActive(bay.key)}
            onBlur={() => setActive(null)}
            onMouseEnter={() => setActive(bay.key)}
            onMouseLeave={() => setActive(null)}
            style={{ cursor: 'pointer', outline: 'none' }}
            opacity={bay.active ? 1 : 0.45}
            data-bay={bay.key}
          >
            <title>
              {[
                `${bay.name}: ${stateLabel(bay.state)}`,
                ...(bay.active ? [] : [t`Inactive equipment`]),
                ...drawn
                  .filter((e) => e.role !== 'status')
                  .map(
                    (e) =>
                      `${elementLabel(e)}: ${valueText(bay.points[expandPointer(e.pointer, bay.key)])}`
                  )
              ].join('\n')}
            </title>
            <rect
              x={centre - slot / 2 + 2}
              y={top + 6}
              width={Math.max(slot - 4, 1)}
              height={area.height + 48}
              rx={6}
              fill={
                chosen
                  ? 'var(--mantine-primary-color-light)'
                  : active === bay.key
                    ? 'var(--mantine-color-gray-light)'
                    : 'transparent'
              }
              stroke={chosen ? 'var(--mantine-primary-color-filled)' : 'none'}
              strokeWidth={1.5}
            />
            {/* Suction riser, and the discharge riser up to the header. */}
            <path
              d={`M${centre} ${bottom}V${pumpY + radius}M${centre + radius} ${pumpY}h${radius * 0.45}V${top}`}
              fill='none'
              stroke={
                flowing
                  ? 'var(--mantine-color-blue-filled)'
                  : 'var(--mimic-line)'
              }
              strokeWidth={flowing ? 2.5 : 1.5}
            />
            {/* Shaft, motor above, pump below. */}
            <line
              x1={centre}
              y1={motor.bottom}
              x2={centre}
              y2={pumpY - radius}
              stroke={style.line}
              strokeWidth={2}
            />
            <rect
              x={centre - motor.width / 2}
              y={motor.top}
              width={motor.width}
              height={motor.bottom - motor.top}
              rx={3}
              fill={style.fill}
              stroke={style.line}
              strokeWidth={1.6}
              strokeDasharray={style.dashed ? '4 3' : undefined}
            />
            <circle
              cx={centre}
              cy={pumpY}
              r={radius}
              fill={style.fill}
              stroke={style.line}
              strokeWidth={1.6}
              strokeDasharray={style.dashed ? '4 3' : undefined}
            />
            <circle cx={centre} cy={pumpY} r={radius * 0.3} fill={style.line} />
            {slot >= 26 && (
              <text
                x={centre}
                y={bottom + 32}
                textAnchor='middle'
                fontSize={12}
                fontWeight={600}
                fill='var(--mantine-color-text)'
              >
                {bay.key}
              </text>
            )}
            {slot >= 40 && (
              <text
                x={centre}
                y={bottom + 47}
                textAnchor='middle'
                fontSize={10}
                fill='var(--mantine-color-dimmed)'
              >
                {usable(reading) ? valueText(reading) : stateLabel(bay.state)}
              </text>
            )}
          </g>
        );
      })}
    </>
  );
}

/**
 * A drawing with the station's readings on it.
 *
 * The drawing itself is one of two files kept beside the layout that places
 * readings on it, and held to geometry by the server's own check of them
 * (`machine_health.mimic_layout.validate_assets`). It is set into the page
 * rather than shown as an image so that it takes the page's colours - a
 * picture of black lines disappears on a dark theme - and so that a pump can
 * be drawn in the colour of its state.
 */
export function MimicSchematic({
  data,
  unit,
  selected,
  onSelect,
  display = valueText,
  onPart
}: Readonly<{
  data: MimicData;
  /** Draw this pump's unit rather than the station. */
  unit?: string;
  selected?: string | null;
  onSelect?: (key: string) => void;
  /** How a reading's value is written in a part's summary. */
  display?: Display;
  /** A part of the pump was chosen, on the drawing or in its key. */
  onPart?: (state: PartState) => void;
}>) {
  // The part being pointed at, on the drawing or in the key beside it.
  const [active, setActive] = useState<string | null>(null);
  const parts = unit ? partStates(data, display) : [];
  const drawing = unit ? unitDrawing : stationDrawing;
  const view = unit ? 'unit' : 'station';
  const points = unit ? data.points : data.station_points;
  const bay = unit ? data.bays.find((b) => b.key === unit) : undefined;
  const state = stateStyle(bay?.state ?? 'unknown');
  const [left, top, width, height] = viewBoxOf(drawing)
    .split(/\s+/)
    .map(Number);

  return (
    <svg
      viewBox={viewBoxOf(drawing)}
      role={unit ? 'img' : 'group'}
      aria-label={unit ? t`Pump unit schematic` : t`Station schematic`}
      style={
        {
          ...THEME,
          '--mimic-state': state.fill,
          '--mimic-state-line': state.line,
          ...partOutlines(parts, active),
          width: '100%',
          aspectRatio: `${width} / ${height}`,
          display: 'block',
          margin: '0 auto',
          fontFamily: 'inherit'
        } as CSSProperties
      }
    >
      {/* The drawing arrives as a whole <svg>, which places itself at the
          origin; moved to where its own viewBox starts, it lines up with the
          tags and bays drawn over it in the same coordinates. */}
      <g
        transform={`translate(${left} ${top})`}
        // biome-ignore lint/security/noDangerouslySetInnerHtml: a drawing shipped with this page, which the server's layout check holds to geometry - no script, text, image or link
        dangerouslySetInnerHTML={{ __html: drawing }}
      />
      {!unit && <Bays data={data} selected={selected} onSelect={onSelect} />}
      {!!parts.length && (
        <MimicParts
          states={parts}
          width={width}
          active={active}
          onActive={setActive}
          onPart={onPart}
          display={display}
        />
      )}
      {data.layout.elements
        .filter((element) => element.view === view)
        .map((element) => {
          const pointer = expandPointer(element.pointer, unit ?? '');
          return (
            <Tag
              key={element.id}
              element={element}
              pointer={pointer}
              point={points[pointer]}
              data={data}
            />
          );
        })}
    </svg>
  );
}
