import { readFileSync } from 'node:fs';
import type {
  MimicData,
  MimicLayout,
  MimicPoint
} from '../../src/pages/assets/health/mimic/types';

// Synthetic readings exercise the checked-in engineering mapping, not a copy
// of the old crowded screen's coordinates or any plant/customer data.
export const layout = JSON.parse(
  readFileSync(
    new URL(
      '../../../backend/InvenTree/machine_health/layouts/pumphouse.layout.json',
      import.meta.url
    ),
    'utf8'
  )
) as MimicLayout;
export const observed = '2026-10-22T06:32:41Z';
export const bearingName =
  'Motor non-drive-end bearing vibration and temperature';
function point(
  pointer: string,
  label: string,
  group: string,
  value: MimicPoint['value'],
  unit = '',
  extra: Partial<MimicPoint> = {}
): MimicPoint {
  return {
    pointer,
    label,
    group,
    value,
    unit,
    quality: 'good',
    observed_at: observed,
    age_seconds: 0,
    unchanged_for_seconds: null,
    reason: value === null ? 'not_published' : null,
    condition: 'unknown',
    thresholds_configured: false,
    ...extra
  };
}

function pointerFor(template: string, unit: string) {
  return template
    .replaceAll('{pump_number}', unit.slice(1))
    .replaceAll('{pump}', unit);
}

function unitPoints(unit: string): Record<string, MimicPoint> {
  const points = Object.fromEntries(
    layout.elements
      .filter((element) => element.view === 'unit')
      .map((element) => {
        const pointer = pointerFor(element.pointer, unit);
        const value =
          element.id === 'pump-pressure'
            ? null
            : element.role === 'status'
              ? 'I'
              : element.id === 'pump-speed'
                ? 0.42
                : 0;
        const engineeringUnit =
          element.id === 'pump-power'
            ? 'MW'
            : element.id === 'pump-speed'
              ? 'rpm'
              : element.id === 'pump-flow'
                ? 'cusec'
                : '';
        return [
          pointer,
          point(
            pointer,
            element.label,
            'Process readings',
            value,
            engineeringUnit
          )
        ];
      })
  );
  for (const [index, part] of (layout.parts ?? []).entries()) {
    // Empty guide-bearing coverage exercises an absent part without implying
    // a fault or fabricating the missing pressure measurement.
    if (part.code === 'PS-GUIDE-BRG') continue;
    const group = part.code === 'PS-MOTOR-NDE-BRG' ? bearingName : part.label;
    const vibration = ['PS-MOTOR-NDE-BRG', 'PS-MOTOR-DE-BRG'].includes(
      part.code
    );
    const valve = ['PS-EOPD-VALVE', 'PS-HOPD-VALVE'].includes(part.code);
    const flow = part.code === 'PS-DISCHARGE';
    for (
      let sensor = 1;
      sensor <= (vibration ? 2 : valve || flow ? 1 : 3);
      sensor++
    ) {
      const pointer = flow
        ? `/pd/${unit}/dv`
        : `/synthetic/${unit}/${part.code}/${sensor}`;
      points[pointer] = point(
        pointer,
        flow ? 'dv' : `${part.label} ${sensor}`,
        group,
        vibration
          ? 0.1 + sensor * 0.02
          : valve
            ? 99
            : flow
              ? 0
              : 39 + index * 0.3 + sensor * 0.2,
        vibration ? 'mm/s' : valve ? 'percent' : flow ? 'cusec' : 'degC',
        { part_code: part.code }
      );
    }
  }
  // Owner-supplied rendering regressions: a rounded negative zero must not
  // grow a sign; source units/labels remain intact in the fixture itself.
  const vibrationPointer = `/synthetic/${unit}/PS-THRUST-BRG/vibration`;
  points[vibrationPointer] = point(
    vibrationPointer,
    'Thrust bearing vibration',
    'Thrust bearing',
    -0.0001,
    'mm/s',
    { part_code: 'PS-THRUST-BRG' }
  );
  return points;
}

export function fixture(unit: string | null): MimicData {
  return {
    station: 17,
    name: 'Synthetic fourteen-bay station',
    generated_at: observed,
    plant_timezone: 'Asia/Riyadh',
    recorded_telemetry_as_live: true,
    last_observed_at: observed,
    reporting: true,
    enabled: true,
    polling_enabled: true,
    source: { pk: 1, name: 'Synthetic regression fixture' },
    last_poll_at: observed,
    last_error_code: '',
    layout,
    station_points: Object.fromEntries(
      layout.elements
        .filter((element) => element.view === 'station')
        .map((element) => [
          element.pointer,
          point(
            element.pointer,
            element.label,
            'Station readings',
            element.id === 'forebay'
              ? 120.972
              : element.role === 'status'
                ? 'I'
                : 0,
            element.id === 'forebay' ? 'm' : ''
          )
        ])
    ),
    bays: Array.from({ length: 14 }, (_, index) => {
      const key = `P${index + 1}`;
      const points = unitPoints(key);
      const power = layout.elements.find(
        (element) => element.id === 'pump-power'
      );
      if (key === 'P3' && power) {
        const pointer = pointerFor(power.pointer, key);
        points[pointer] = {
          ...points[pointer],
          value: null,
          reason: 'not_published'
        };
      }
      return {
        key,
        machine: index + 1,
        name: `Synthetic pump ${index + 1}`,
        active: true,
        state: 'idle',
        alarm: null,
        points
      };
    }),
    selected_unit: unit,
    points: unit ? unitPoints(unit) : {},
    totals: {
      power: {
        value: null,
        unit: 'MW',
        derived: true,
        reason: 'incomplete',
        contributors: []
      },
      flow: {
        value: 0,
        unit: 'm³/s',
        derived: true,
        reason: null,
        contributors: []
      }
    },
    alarms: [],
    unconfigured_thresholds: 686
  };
}
