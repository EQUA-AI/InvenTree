import { t } from '@lingui/core/macro';

import type {
  HealthState,
  MachineSignal,
  SeriesEntry
} from '@lib/types/MachineHealth';

import type { KpiTile } from './KpiStrip';
import { type ResolvedParameter, withKey, withRole } from './resolve';

const MAX_TILES = 8;

function instant(signal: MachineSignal): number | null {
  if (!signal.observed_at) return null;
  const ms = new Date(signal.observed_at).getTime();
  return Number.isFinite(ms) ? ms : null;
}

/** A reading a tile may show as the machine's state: numeric and good. */
function usableSignal(signal: MachineSignal): boolean {
  return numeric(signal) !== null && signal.quality === 'good';
}

function numeric(signal: MachineSignal): number | null {
  return typeof signal.value === 'number' && Number.isFinite(signal.value)
    ? signal.value
    : null;
}

function tileFor(
  key: string,
  parameter: ResolvedParameter,
  series: Map<number, SeriesEntry>,
  label?: string
): KpiTile {
  const signal = parameter.signal;
  return {
    key,
    label: label ?? parameter.label,
    value: numeric(signal),
    unit: signal.unit,
    decimals: parameter.definition.decimals,
    observedAt: instant(signal),
    stale: signal.stale,
    quality: signal.quality,
    // A limit verdict on an unusable reading is no verdict.
    state:
      hasLimits(signal) && signal.quality === 'good'
        ? signal.state
        : 'unconfigured',
    series: series.get(signal.binding_id)
  };
}

function hasLimits(signal: MachineSignal): boolean {
  const l = signal.limits;
  return (
    l.normal_min !== null ||
    l.normal_max !== null ||
    l.warn_min !== null ||
    l.warn_max !== null ||
    l.critical_min !== null ||
    l.critical_max !== null
  );
}

/** The highest current reading in a family, as one tile. */
function highestTile(
  key: string,
  label: string,
  family: ResolvedParameter[],
  series: Map<number, SeriesEntry>,
  missingReason: string
): KpiTile {
  // Only good-quality readings compete: a pegged sensor reporting 3276.7 with
  // quality bad is the loudest number in the family and means nothing.
  const live = family.filter((p) => usableSignal(p.signal) && !p.signal.stale);
  const pool =
    live.length > 0 ? live : family.filter((p) => usableSignal(p.signal));
  if (pool.length === 0) {
    return {
      key,
      label,
      value: null,
      unit: '',
      decimals: 1,
      observedAt: null,
      stale: false,
      state: 'unconfigured',
      missingReason
    };
  }
  const top = pool.reduce((a, b) =>
    (numeric(b.signal) as number) > (numeric(a.signal) as number) ? b : a
  );
  const worst = pool
    .map((p) => (hasLimits(p.signal) ? p.signal.state : 'unconfigured'))
    .reduce<HealthState | 'unconfigured'>((acc, state) => {
      const rank = {
        critical: 4,
        warning: 3,
        normal: 2,
        offline: 1,
        unknown: 1,
        unconfigured: 0
      };
      return rank[state] > rank[acc] ? state : acc;
    }, 'unconfigured');
  return {
    key,
    label,
    value: numeric(top.signal),
    unit: top.signal.unit,
    decimals: top.definition.decimals,
    observedAt: instant(top.signal),
    stale: top.signal.stale,
    state: worst,
    series: series.get(top.signal.binding_id),
    caption: top.signal.unit
      ? t`Highest of ${pool.length}: ${top.label}`
      : t`Highest of ${pool.length}: ${top.label} · unit unconfirmed, raw source value`
  };
}

function statusTile(
  parameter: ResolvedParameter,
  series: Map<number, SeriesEntry>
): KpiTile {
  const entry = series.get(parameter.signal.binding_id);
  const last = entry?.available
    ? entry.samples[entry.samples.length - 1]
    : undefined;
  const drawn = last?.v ?? null;
  const code =
    typeof parameter.signal.value === 'string' ? parameter.signal.value : null;
  // The server maps the plant's status vocabulary onto numbers in the series;
  // until that read lands the tile shows the code itself rather than guessing.
  const word =
    drawn === null
      ? code
        ? t`Code ${code}`
        : t`Unknown`
      : drawn > 0
        ? t`Running`
        : drawn < 0
          ? t`Fault`
          : t`Idle`;
  return {
    key: 'status',
    label: parameter.label,
    value: null,
    valueText: word,
    unit: '',
    decimals: 0,
    observedAt: last?.t ?? instant(parameter.signal),
    stale: parameter.signal.stale,
    state: 'unconfigured',
    caption:
      typeof parameter.signal.value === 'string'
        ? t`Plant status code: ${parameter.signal.value}`
        : undefined
  };
}

/**
 * Level 1: the eight numbers an operator reads first.
 *
 * In the order the plant thinks about a pump - power, speed, pressure,
 * vibration, current, voltage, power factor, hottest winding - then status and
 * flow if there is room. A parameter the pump has not got is left out, with one
 * exception: vibration keeps its tile and says why it is empty, because a pump
 * with no usable vibration channel is something an operator should see.
 */
export function buildKpiTiles(
  parameters: ResolvedParameter[],
  series: Map<number, SeriesEntry>
): KpiTile[] {
  const tiles: KpiTile[] = [];
  const add = (tile: KpiTile | null | undefined) => {
    if (tile) tiles.push(tile);
  };
  const single = (key: string, catalogueKey: string) => {
    const p = withKey(parameters, catalogueKey);
    return p ? tileFor(key, p, series) : null;
  };

  add(single('power', 'ACTIVE_POWER'));
  add(single('speed', 'SPEED'));
  add(single('pressure', 'DISCHARGE_PRESSURE'));

  const vibration = withRole(
    parameters,
    'nde_vibration',
    'de_vibration',
    'thrust_vibration',
    'vibration',
    'velometer'
  );
  add(
    highestTile(
      'vibration',
      t`Vibration`,
      vibration,
      series,
      t`No approved vibration channel`
    )
  );

  add(single('current', 'CURRENT_AVG'));
  add(single('voltage', 'LINE_VOLTAGE'));
  add(single('power_factor', 'POWER_FACTOR'));

  const winding = withRole(parameters, 'winding');
  if (winding.length > 0) {
    add(highestTile('winding', t`Winding temperature`, winding, series, ''));
  }

  const status = withRole(parameters, 'status')[0];
  if (status) add(statusTile(status, series));
  add(single('flow', 'DISCHARGE_FLOW'));
  add(single('frequency', 'FREQUENCY'));

  return tiles.slice(0, MAX_TILES);
}
