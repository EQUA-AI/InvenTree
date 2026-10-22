import { t } from '@lingui/core/macro';
import { type CSSProperties, useLayoutEffect, useRef, useState } from 'react';
import unitDrawing from '../../../../assets/mimic/pump-unit.svg?raw';
import { ValueTooltip } from '../../../../components/items/ValueTooltip';
import { freshnessText } from './FreshnessLabel';
import {
  MimicPartMarkers,
  MimicParts,
  type PartState,
  partOutlines,
  partStates
} from './MimicParts';
import type { Display } from './MimicReadings';
import { UnavailableReading, failedReading } from './MimicValue';
import classes from './UnitMimic.module.css';
import {
  elementLabel,
  expandPointer,
  reasonLabel,
  stateLabel,
  usable
} from './format';
import { formatInstant, useDisplayClock } from './observedTime';
import type { MimicData } from './types';

/** The native geometry occupies these bounds; its coordinates are not changed. */
const UNIT_VIEWBOX = '214 18 676 510';
const NATIVE_DRAWING = unitDrawing.replace(
  '<svg ',
  '<svg width="1100" height="580" '
);

export function UnitSchematic({
  data,
  unit,
  display,
  onPart,
  state
}: Readonly<{
  data: MimicData;
  unit: string;
  display: Display;
  onPart?: (state: PartState) => void;
  state: { fill: string; line: string; dashed?: boolean };
}>) {
  const [active, setActive] = useState<string | null>(null);
  const drawing = useRef<SVGSVGElement>(null);
  const [markerScale, setMarkerScale] = useState(1);
  useLayoutEffect(() => {
    const node = drawing.current;
    if (!node) return;
    const fit = () => {
      const transform = node.getScreenCTM();
      const scale = transform ? Math.hypot(transform.a, transform.b) : 0;
      if (scale > 0) setMarkerScale(Math.max(1, 0.88 / scale));
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(node);
    if (node.parentElement) observer.observe(node.parentElement);
    window.addEventListener('resize', fit);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', fit);
    };
  }, [unit]);
  const parts = partStates(data, display);
  // Match the side and vertical order of the native markers. Catalogue
  // numbering remains unchanged on both the drawing and the reading groups.
  const spatialOrder = (a: PartState, b: PartState) =>
    a.part.y - b.part.y || a.part.x - b.part.x;
  const leftParts = parts.filter(({ part }) => part.x < 550).sort(spatialOrder);
  const rightParts = parts
    .filter(({ part }) => part.x >= 550)
    .sort(spatialOrder);
  const bay = data.bays.find((item) => item.key === unit);
  const clock = useDisplayClock();
  const elements = data.layout.elements.filter((element) => {
    if (element.view !== 'unit') return false;
    const point = data.points[expandPointer(element.pointer, unit)];
    // The heading already states the pump's known state. Preserve an unmapped
    // source code rather than replacing it with the bay's unknown state.
    return (
      element.role !== 'status' ||
      !usable(point) ||
      display(point) !== stateLabel(bay?.state ?? 'unknown')
    );
  });
  return (
    <div data-unit-schematic>
      <dl className={classes.metrics} aria-label={t`Key readings`}>
        {elements.map((element) => {
          const pointer = expandPointer(element.pointer, unit);
          const point = data.points[pointer];
          const label = elementLabel(element);
          const shown = usable(point) && !failedReading(point);
          const reason =
            reasonLabel(point?.reason ?? null) || t`Reason not supplied`;
          const observed = formatInstant(point?.observed_at, clock);
          const detail = shown
            ? `${label}: ${display(point)}${observed ? ` · ${observed} · ${clock.zone} · ${freshnessText(point, clock)}` : ''}`
            : `${label}: ${reason}${observed ? ` · ${observed} · ${clock.zone} · ${freshnessText(point, clock)}` : ''}`;
          return (
            <div
              key={element.id}
              className={classes.metric}
              data-point={pointer}
              data-reading
            >
              <dt className={classes.metricLabel} data-tag-label>
                {label}
              </dt>
              <dd className={classes.metricValue} data-value>
                {shown ? (
                  <ValueTooltip label={detail} className={classes.valueTarget}>
                    {display(point)}
                  </ValueTooltip>
                ) : (
                  <UnavailableReading
                    point={point}
                    label={label}
                    display={display}
                  />
                )}
              </dd>
            </div>
          );
        })}
      </dl>
      <div
        className={classes.workspace}
        data-has-parts={!!parts.length || undefined}
      >
        <svg
          ref={drawing}
          className={classes.drawing}
          data-unit-drawing
          viewBox={UNIT_VIEWBOX}
          role='img'
          aria-label={t`Pump unit schematic`}
          style={
            {
              '--mimic-line':
                'light-dark(var(--mantine-color-gray-7), var(--mantine-color-dark-1))',
              '--mimic-muted':
                'light-dark(var(--mantine-color-gray-6), var(--mantine-color-dark-2))',
              '--mimic-surface':
                'light-dark(var(--mantine-color-gray-0), var(--mantine-color-dark-6))',
              '--mimic-structure': 'var(--mantine-color-default-border)',
              '--mimic-water': 'var(--mantine-color-body)',
              '--mimic-state': state.fill,
              '--mimic-state-line': state.line,
              '--mimic-state-dash': state.dashed ? '6 4' : 'none',
              ...partOutlines(parts, active)
            } as CSSProperties
          }
        >
          <defs>
            <pattern
              id='mimic-unit-hatch'
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
          <g
            // biome-ignore lint/security/noDangerouslySetInnerHtml: checked-in native SVG with geometry validated against the backend layout; no user content
            dangerouslySetInnerHTML={{ __html: NATIVE_DRAWING }}
          />
          <MimicPartMarkers
            states={parts}
            active={active}
            scale={markerScale}
            onPart={onPart}
            onActive={setActive}
          />
        </svg>
        <div className={classes.callouts} data-unit-callouts>
          <div className={classes.column} data-callout-column='left'>
            <MimicParts
              states={leftParts}
              active={active}
              onActive={setActive}
              onPart={onPart}
              display={display}
            />
          </div>
          <div className={classes.column} data-callout-column='right'>
            <MimicParts
              states={rightParts}
              active={active}
              onActive={setActive}
              onPart={onPart}
              display={display}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
