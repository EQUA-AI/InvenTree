import { t } from '@lingui/core/macro';
import {
  type CSSProperties,
  type KeyboardEvent,
  useLayoutEffect,
  useRef,
  useState
} from 'react';
import { ageLabel, freshnessText } from './FreshnessLabel';
import { UnitSchematic } from './UnitSchematic';
import { useDisplayClock } from './observedTime';

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
const TAG = { width: 230, height: 60 };

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
      line: 'var(--mantine-color-green-8)'
    },
    idle: {
      fill: 'var(--mantine-color-body)',
      line: 'var(--mimic-stale-line, var(--mantine-color-text))'
    },
    fault: {
      fill: 'var(--mantine-color-red-light)',
      line: 'var(--mantine-color-red-filled)'
    },
    stale: {
      fill: 'url(#mimic-hatch)',
      line: 'var(--mimic-stale-line, var(--mantine-color-text))',
      dashed: true
    }
  };
const UNKNOWN = {
  fill: 'transparent',
  line: 'var(--mantine-color-text)',
  dashed: true
};

export function stateStyle(state: string) {
  return STATE[state] ?? UNKNOWN;
}

/**
 * The colour an alarm is marked in on the drawing.
 *
 * Kept apart from the bay's state on purpose. Running and idle are what the
 * pump is doing; an alarm is what one of its readings is doing, and a running
 * pump with a hot winding is still running. So the bay keeps the colour of
 * its state and gains a marker and a ring in the colour of its alarm.
 */
export function alarmColor(alarm?: string | null) {
  return alarm === 'critical'
    ? 'var(--mantine-color-red-9)'
    : alarm === 'warning'
      ? 'var(--mantine-color-orange-9)'
      : null;
}

export function alarmLabel(alarm?: string | null) {
  return alarm === 'critical'
    ? t`Critical alarm`
    : alarm === 'warning'
      ? t`Warning alarm`
      : '';
}

/** The drawings name their colours; this is what the page gives them. */
const THEME = {
  '--mimic-line': 'var(--mantine-color-text)',
  '--mimic-muted': 'var(--mantine-color-text)',
  '--mimic-surface': 'var(--mantine-color-body)',
  '--mimic-structure': 'var(--mantine-color-default-border)',
  '--mimic-water': 'var(--mantine-color-body)'
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
  if (point.condition === 'normal') return 'var(--mantine-color-green-8)';
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
  data,
  renderWidth
}: Readonly<{
  renderWidth: number;
  element: LayoutElement;
  pointer: string;
  point?: MimicPoint;
  data: MimicData;
}>) {
  // Reading tags may move on the reference drawing to preserve legibility;
  // their leader targets and dictionary mappings remain the layout's.
  const discharge = ['pump-pressure', 'pump-flow'].includes(element.id);
  const width = discharge ? 270 : TAG.width;
  const x = discharge
    ? 566
    : element.id === 'pump-speed'
      ? 254
      : element.id === 'pump-status'
        ? 624
        : element.x;
  const stale = point?.reason === 'stale';
  const y =
    element.id === 'pump-pressure'
      ? stale
        ? 425
        : 455
      : element.id === 'pump-flow' && stale
        ? 502
        : element.y;
  const height = stale ? 76 : TAG.height;
  const labelSize = Math.max(
    14,
    ((window.innerWidth >= 1440 ? 12 : 11) * 1100) / renderWidth
  );
  const valueSize = Math.max(17, (14 * 1100) / renderWidth);
  const clock = useDisplayClock(data.plant_timezone);
  const shown = usable(point);
  const text =
    element.role === 'status'
      ? statusText(point, data.layout)
      : valueText(point);
  // Only a *missing* point is unbound; a present one states its own reason.
  const reason = point ? reasonLabel(point.reason) : reasonLabel('not_bound');
  const split = reason.lastIndexOf(' ', 18);
  const reasonLines =
    !shown && !stale && reason.length > 18
      ? [
          reason.slice(0, split > 0 ? split : 18),
          reason.slice(split > 0 ? split + 1 : 18)
        ]
      : null;
  const accent = conditionLine(point);
  const hasTarget = element.ax !== undefined && element.ay !== undefined;
  const from = hasTarget && {
    x: Math.min(Math.max(element.ax as number, x), x + width),
    y: Math.min(Math.max(element.ay as number, y), y + height)
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
            stroke='var(--mantine-color-text)'
            strokeWidth={1.5}
          />
          <circle
            cx={element.ax}
            cy={element.ay}
            r={2.5}
            fill='var(--mantine-color-text)'
          />
        </>
      )}
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx={6}
        fill='var(--mantine-color-body)'
        stroke={accent ?? 'var(--mantine-color-default-border)'}
        strokeWidth={accent ? 2 : 1.2}
        strokeDasharray={shown ? undefined : '4 3'}
      />
      <text
        data-tag-label
        x={x + 10}
        y={y + 23}
        fontSize={labelSize}
        fill='var(--mantine-color-text)'
      >
        {elementLabel(element)}
      </text>
      {point?.observed_at &&
        point.age_seconds != null &&
        Number.isFinite(point.age_seconds) && (
          <text
            data-tag-age
            x={x + width - 8}
            y={y + 23}
            textAnchor='end'
            fontSize={Math.max(12, (10 * 1100) / renderWidth)}
            fill='var(--mantine-color-text)'
          >
            {ageLabel(point.age_seconds)}
          </text>
        )}
      <text
        x={x + 10}
        y={y + (stale ? 47 : 51)}
        fontSize={stale || !shown ? labelSize : valueSize}
        fontWeight={shown ? 600 : 400}
        fontStyle={shown ? undefined : 'italic'}
        fill={shown ? 'var(--mantine-color-text)' : 'var(--mantine-color-text)'}
      >
        {reasonLines
          ? reasonLines.map((line, index) => (
              <tspan key={line} x={x + 10} y={y + 40 + index * 17}>
                {line}
              </tspan>
            ))
          : point?.reason === 'stale'
            ? t`Stale · last observed`
            : shown
              ? text
              : reason || text}
      </text>
      {point?.reason === 'stale' && (
        <text
          x={x + 10}
          y={y + 69}
          fontSize={Math.max(12, (10 * 1100) / renderWidth)}
          fill='var(--mantine-color-text)'
        >
          {point.observed_at
            ? freshnessText(point, clock)
                .split('last observed ')[1]
                ?.split(' (')[0]
            : t`Time unavailable`}
        </text>
      )}
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
        const alarm = alarmColor(bay.alarm);
        const chosen = selected === bay.key;
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
            aria-label={
              alarm
                ? `${bay.key}: ${stateLabel(bay.state)}, ${alarmLabel(bay.alarm)}`
                : `${bay.key}: ${stateLabel(bay.state)}`
            }
            onClick={() => onSelect?.(bay.key)}
            onKeyDown={press}
            onFocus={() => setActive(bay.key)}
            onBlur={() => setActive(null)}
            onMouseEnter={() => setActive(bay.key)}
            onMouseLeave={() => setActive(null)}
            style={{ cursor: 'pointer', outline: 'none' }}
            opacity={bay.active ? 1 : 0.45}
            data-bay={bay.key}
            data-alarm={bay.alarm ?? undefined}
          >
            <title>
              {[
                `${bay.name}: ${stateLabel(bay.state)}`,
                ...(alarm ? [alarmLabel(bay.alarm)] : []),
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
              stroke={
                alarm ??
                (chosen ? 'var(--mantine-primary-color-filled)' : 'none')
              }
              strokeWidth={alarm ? 2 : 1.5}
            />
            {/* Suction riser, and the discharge riser up to the header. */}
            <path
              d={`M${centre} ${bottom}V${pumpY + radius}M${centre + radius} ${pumpY}h${radius * 0.45}V${top}`}
              fill='none'
              stroke='var(--mimic-line)'
              strokeWidth={1.5}
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
            {/* The alarm, over the motor: seen from across the room, which is
                where a station drawing is read from. */}
            {alarm && (
              <g data-alarm-marker={bay.alarm}>
                <circle
                  cx={centre}
                  cy={top + 20}
                  r={8}
                  fill={alarm}
                  stroke='var(--mantine-color-text)'
                  strokeWidth={1.5}
                />
                <text
                  x={centre}
                  y={top + 24}
                  textAnchor='middle'
                  fontSize={14}
                  fontWeight={700}
                  fill={
                    bay.alarm === 'warning'
                      ? 'var(--mantine-color-black)'
                      : 'var(--mantine-color-white)'
                  }
                >
                  !
                </text>
              </g>
            )}
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
                fill='var(--mantine-color-text)'
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
function LegacyMimicSchematic({
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
  const svg = useRef<SVGSVGElement>(null);
  const [renderWidth, setRenderWidth] = useState(1100);
  const [fitWidth, setFitWidth] = useState<number | undefined>();
  useLayoutEffect(() => {
    const node = svg.current;
    if (!node) return;
    const fit = () => {
      if (window.innerWidth < 768) {
        setFitWidth(undefined);
        setRenderWidth(Math.max(720, node.parentElement?.clientWidth ?? 720));
        return;
      }
      const parentWidth = node.parentElement?.clientWidth ?? 1100;
      const available = Math.max(
        180,
        window.innerHeight -
          (node.getBoundingClientRect().top + window.scrollY) -
          16
      );
      const next = Math.min(
        parentWidth,
        Math.max(unit ? 600 : 0, available * (unit ? 1100 / 580 : 1100 / 300))
      );
      setFitWidth(next);
      setRenderWidth(next);
    };
    fit();
    window.addEventListener('resize', fit);
    const observer = new ResizeObserver(fit);
    if (node.parentElement) observer.observe(node.parentElement);
    return () => {
      window.removeEventListener('resize', fit);
      observer.disconnect();
    };
  }, [unit, data.display_shifted, data.last_error_code, data.enabled]);
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
      ref={svg}
      data-render-width={renderWidth}
      viewBox={viewBoxOf(drawing)}
      role={unit ? 'img' : 'group'}
      aria-label={unit ? t`Pump unit schematic` : t`Station schematic`}
      style={
        {
          ...THEME,
          '--mimic-state': state.fill,
          '--mimic-state-line': state.line,
          '--mimic-state-dash': state.dashed ? '6 4' : 'none',
          ...partOutlines(parts, active),
          width: fitWidth ?? '100%',
          maxWidth: '100%',

          aspectRatio: `${width} / ${height}`,
          display: 'block',
          margin: '0 auto',
          fontFamily: 'inherit'
        } as CSSProperties
      }
    >
      <defs>
        <pattern
          id='mimic-hatch'
          width='8'
          height='8'
          patternUnits='userSpaceOnUse'
        >
          <rect width='8' height='8' fill='var(--mantine-color-body)' />
          <path
            d='M-2 2L2 -2M0 8L8 0M6 10L10 6'
            stroke='var(--mimic-hatch-line, var(--mantine-color-gray-6))'
            strokeWidth='1.5'
          />
        </pattern>
      </defs>
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
              renderWidth={renderWidth}
            />
          );
        })}
    </svg>
  );
}

/** Unit callouts and readings use the page grid, outside the drawing. */
export function MimicSchematic(
  props: Readonly<{
    data: MimicData;
    unit?: string;
    selected?: string | null;
    onSelect?: (key: string) => void;
    display?: Display;
    onPart?: (state: PartState) => void;
  }>
) {
  if (props.unit) {
    const bay = props.data.bays.find((item) => item.key === props.unit);
    const state = stateStyle(bay?.state ?? 'unknown');
    return (
      <UnitSchematic
        data={props.data}
        unit={props.unit}
        display={props.display ?? valueText}
        onPart={props.onPart}
        state={{
          ...state,
          fill: bay?.state === 'stale' ? 'url(#mimic-unit-hatch)' : state.fill
        }}
      />
    );
  }
  return <LegacyMimicSchematic {...props} />;
}
