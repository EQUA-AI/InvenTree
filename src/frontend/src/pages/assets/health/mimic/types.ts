export type MimicPoint = {
  pointer: string;
  label: string;
  /** The part the reading belongs to, as this pump's registry names it. */
  group: string;
  /** The same part's catalogue code, which every station shares. */
  part_code?: string;
  value: string | number | boolean | null;
  unit: string;
  quality: string;
  observed_at: string | null;
  age_seconds: number | null;
  unchanged_for_seconds: number | null;
  reason: string | null;
  condition: string;
  thresholds_configured: boolean;
};

/**
 * A reading outside its limits, and the alarm the detector raised from it.
 *
 * `anomaly` is null while the reading breaches but no alarm stands yet - the
 * poller has not evaluated it, or its vote group has not agreed. `machine` is
 * the pump or station the reading belongs to, where the alarm lives.
 */
export type MimicAlarm = MimicPoint & {
  machine?: number;
  anomaly: number | null;
  anomaly_status: 'open' | 'acknowledged' | null;
  severity: string | null;
};

export type LayoutElement = {
  id: string;
  pointer: string;
  view: string;
  label: string;
  role: string;
  /** Top-left corner of the reading's tag, in the drawing's own units. */
  x: number;
  y: number;
  /** Where on the drawing the reading is taken, if the tag points at it. */
  ax?: number;
  ay?: number;
};

/**
 * A part of the pump, as a region of the unit drawing.
 *
 * `id` is the region's id in the drawing, `code` the catalogue code of the
 * part drawn there, and `x`,`y` where its marker goes.
 */
export type LayoutPart = {
  id: string;
  code: string;
  label: string;
  x: number;
  y: number;
};

/** The part of the station drawing left free for its pump bays. */
export type BayArea = { x: number; y: number; width: number; height: number };

export type Bay = {
  key: string;
  /** The pump slot's own machine, for linking to its pages. */
  machine?: number;
  name: string;
  active: boolean;
  state: string;
  points: Record<string, MimicPoint>;
};

export type Total = {
  value: number | null;
  unit: string;
  derived: boolean;
  reason: string | null;
  contributors: string[];
};

export type MimicLayout = {
  version: number;
  review_status: string;
  /** Which source codes mean which state, e.g. `R` is running. */
  status_values?: Record<string, string[]>;
  bays?: BayArea;
  parts?: LayoutPart[];
  elements: LayoutElement[];
};

export type MimicData = {
  station: number;
  name: string;
  generated_at: string;
  enabled: boolean;
  source: { pk: number; name: string } | null;
  last_poll_at: string | null;
  last_error_code: string;
  layout: MimicLayout;
  station_points: Record<string, MimicPoint>;
  bays: Bay[];
  selected_unit: string | null;
  points: Record<string, MimicPoint>;
  totals: Record<string, Total>;
  alarms: MimicAlarm[];
  unconfigured_thresholds: number;
};
