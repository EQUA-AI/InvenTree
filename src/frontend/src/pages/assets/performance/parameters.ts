import { t } from '@lingui/core/macro';

/**
 * The parameter catalogue behind the Performance tab.
 *
 * A pump reports sixty-odd tags and three stations name them three different
 * ways: Cedar Creek says `PUMP_MOTOR_WINDING_TEMPERATURED7`, Maple Grove says
 * `PUMP_MOTOR_WINDING_TEMP7`, Millbrook's stator RTDs are `MT_STTR_WND_RTD3`.
 * The dashboard must not know any of that. It knows *parameters* - "winding
 * temperature, sensor 7" - and this catalogue is the one place a tag name is
 * turned into one.
 *
 * Three rules:
 *
 * - **Units come from the dictionary, never from here.** A definition carries
 *   no unit. The reviewed dictionary point behind each binding is the authority
 *   on what a tag measures in, and a tag whose unit has not been confirmed is
 *   not bound at all, so it never reaches this page. `decimals` is presentation.
 * - **No thresholds live here either.** Limits are configured on the binding
 *   and arrive with the signal. A parameter with none is drawn without any.
 * - **Matching is on the tag, not the display name.** Display names are
 *   whatever a reviewer typed; the tag is what the plant wrote.
 *
 * Adding a station with new naming means adding a pattern here, not touching
 * a component.
 */

export type ParameterCategory =
  | 'electrical'
  | 'operation'
  | 'hydraulic'
  | 'winding'
  | 'vibration'
  | 'cooling'
  | 'bearing'
  | 'station'
  | 'other';

export type ChartKind = 'line' | 'area' | 'step';

/** The KPI tile a parameter feeds, if any. Derived tiles are computed in code. */
export type KpiSlot =
  | 'power'
  | 'speed'
  | 'pressure'
  | 'current'
  | 'voltage'
  | 'power_factor'
  | 'frequency'
  | 'flow'
  | 'status';

export interface ParameterDefinition {
  /** Canonical key, stable across stations. */
  key: string;
  /** Human label; a sensor index or side is appended by the resolver. */
  label: () => string;
  category: ParameterCategory;
  /**
   * The engineering role within its category. Sensors sharing a role are one
   * family - drawn on one axis, compared in one heatmap.
   */
  role: string;
  /**
   * Tested against the tag with its pump prefix removed. Capture group 1, when
   * present, is the sensor index; a named group `side` is a left/right or
   * upper/lower position.
   */
  match: RegExp;
  /** Display precision. Presentation only; never a claim about the sensor. */
  decimals: number;
  chart: ChartKind;
  kpi?: KpiSlot;
  /** What the parameter is, for a tooltip; optional. */
  description?: () => string;
}

/**
 * Order matters: the first pattern that matches wins, so specific patterns sit
 * before general ones.
 */
export const PARAMETERS: readonly ParameterDefinition[] = [
  // ---------------------------------------------------------------- electrical
  {
    key: 'ACTIVE_POWER',
    label: () => t`Active power`,
    category: 'electrical',
    role: 'active_power',
    match: /^ACTIVE_POWER$/i,
    decimals: 2,
    chart: 'area',
    kpi: 'power'
  },
  {
    key: 'REACTIVE_POWER',
    label: () => t`Reactive power`,
    category: 'electrical',
    role: 'reactive_power',
    match: /^PUMP_REACTIVE_POWER$/i,
    decimals: 2,
    chart: 'line'
  },
  {
    key: 'POWER_FACTOR',
    label: () => t`Power factor`,
    category: 'electrical',
    role: 'power_factor',
    match: /^PUMP_POWERFATCOR$/i,
    decimals: 3,
    chart: 'line',
    kpi: 'power_factor'
  },
  {
    key: 'CURRENT_AVG',
    label: () => t`Motor current`,
    category: 'electrical',
    role: 'current',
    match: /^PUMP_CURRENT_AVG$/i,
    decimals: 1,
    chart: 'line',
    kpi: 'current'
  },
  {
    key: 'PHASE_CURRENT',
    label: () => t`Phase current`,
    category: 'electrical',
    role: 'phase_current',
    match: /^PUMP_(?<side>[RYB])-PH_CURRENT$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'LINE_VOLTAGE',
    label: () => t`Line-to-line voltage`,
    category: 'electrical',
    role: 'voltage',
    match: /^PUMP_LINE_TO_LINE_VOLTAGE$/i,
    decimals: 0,
    chart: 'line',
    kpi: 'voltage'
  },
  {
    key: 'PHASE_VOLTAGE',
    label: () => t`Phase voltage`,
    category: 'electrical',
    role: 'phase_voltage',
    match: /^PUMP_(?<side>RY|YB|BR)_VOLTAGE$/i,
    decimals: 0,
    chart: 'line'
  },
  {
    key: 'FREQUENCY',
    label: () => t`Frequency`,
    category: 'electrical',
    role: 'frequency',
    match: /^PUMP_FREQUENCY$/i,
    decimals: 2,
    chart: 'line',
    kpi: 'frequency'
  },
  {
    key: 'EXCITATION_CURRENT',
    label: () => t`Excitation field current`,
    category: 'electrical',
    role: 'excitation_current',
    match: /^EXCITATION_FLD_CURR(?:_PROCESS_VALUE|\.PV)?$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'EXCITATION_VOLTAGE',
    label: () => t`Excitation field voltage`,
    category: 'electrical',
    role: 'excitation_voltage',
    match: /^EXCITATION_FLD_VLTG(?:_PROCESS_VALUE|\.PV)?$/i,
    decimals: 1,
    chart: 'line'
  },

  // ----------------------------------------------------------------- operation
  {
    key: 'SPEED',
    label: () => t`Shaft speed`,
    category: 'operation',
    role: 'speed',
    match: /^SPEED$/i,
    decimals: 0,
    chart: 'line',
    kpi: 'speed'
  },
  {
    key: 'EQUIPMENT_STATUS',
    label: () => t`Equipment status`,
    category: 'operation',
    role: 'status',
    match: /^pd:st$/i,
    decimals: 0,
    chart: 'step',
    kpi: 'status',
    description: () => t`The plant's own run/stop status for this bay.`
  },
  {
    key: 'MOTOR_STATUS',
    label: () => t`Motor status`,
    category: 'operation',
    role: 'motor_status',
    match: /^MOTOR_(?<side>ON|OFF)_STATUS$/i,
    decimals: 0,
    chart: 'step'
  },
  {
    key: 'EOPD_VALVE',
    label: () => t`EOPD valve position`,
    category: 'operation',
    role: 'valve',
    match: /^EOPD_VALVE_POS(?:_PROCESS_VALUE|\.PV)?$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'HOPD_VALVE',
    label: () => t`HOPD valve position`,
    category: 'operation',
    role: 'valve',
    match: /^HOPD_VALVE_POS(?:_PROCESS_VALUE|\.PV)?$/i,
    decimals: 1,
    chart: 'line'
  },

  // ----------------------------------------------------------------- hydraulic
  {
    key: 'DISCHARGE_PRESSURE',
    label: () => t`Discharge pressure`,
    category: 'hydraulic',
    role: 'pressure',
    match: /^(?:DISCHARGE_)?PRESSURE$/i,
    decimals: 2,
    chart: 'line',
    kpi: 'pressure'
  },
  {
    key: 'DISCHARGE_FLOW',
    label: () => t`Discharge flow`,
    category: 'hydraulic',
    role: 'flow',
    match: /^pd:dv$/i,
    decimals: 1,
    chart: 'area',
    kpi: 'flow'
  },
  {
    key: 'DRAFT_TUBE',
    label: () => t`Draft tube`,
    category: 'hydraulic',
    role: 'draft_tube',
    match: /DRAFT_TUBE|_DRF_TB_/i,
    decimals: 2,
    chart: 'line'
  },
  {
    key: 'SPIRAL_CASING',
    label: () => t`Spiral casing`,
    category: 'hydraulic',
    role: 'spiral_casing',
    match: /^SPIRAL_CAS(?:E|ING)-?(\d+)$/i,
    decimals: 2,
    chart: 'line'
  },
  {
    key: 'DELIVERY_LINE',
    label: () => t`Delivery line`,
    category: 'hydraulic',
    role: 'delivery_line',
    match: /MV_PUMP_DELIVERY_LINE|MV_PMP_DLV/i,
    decimals: 2,
    chart: 'line'
  },

  // ------------------------------------------------------------------- winding
  {
    key: 'WINDING_TEMP',
    label: () => t`Winding`,
    category: 'winding',
    role: 'winding',
    match: /^PUMP_MOTOR_WINDING_TEMP(?:ERATURE)?D?(\d+)$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'STATOR_WINDING_RTD',
    label: () => t`Stator winding RTD`,
    category: 'winding',
    role: 'winding',
    match: /^MT_STTR_WND_RTD(\d+)(?:_PROCESS_VALUE|\.PV)?$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'CORE_TEMP',
    label: () => t`Stator core`,
    category: 'winding',
    role: 'core',
    match: /^MOTOR_(?:STATOR_)?CORE_RTD(\d+)(?:_PROCESS_VALUE)?$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'BRUSH_GEAR',
    label: () => t`Brush gear`,
    category: 'winding',
    role: 'motor_misc',
    match: /^PUMP_BRUSH_GEARD\d+$/i,
    decimals: 1,
    chart: 'line'
  },

  // ----------------------------------------------------------------- vibration
  {
    key: 'NDE_BEARING_VIBRATION',
    label: () => t`Motor NDE bearing vibration`,
    category: 'vibration',
    role: 'nde_vibration',
    match:
      /^(?:MTR_NDE_BRG_VBRTN|MOTOR_NDE_BEARING_VIBRATION)(\d+)(?:_PROCESS_VALUE|\.PV)?$/i,
    decimals: 2,
    chart: 'line'
  },
  {
    key: 'DE_VIBRATION',
    label: () => t`Motor DE vibration`,
    category: 'vibration',
    role: 'de_vibration',
    match: /^(?:PUMP_)?MOTOR_DE_VIBRATION(\d+)$/i,
    decimals: 2,
    chart: 'line'
  },
  {
    key: 'THRUST_BEARING_VIBRATION',
    label: () => t`Thrust bearing vibration`,
    category: 'vibration',
    role: 'thrust_vibration',
    match:
      /^(?:PMP_THRST_BRG_VBRTN|PUMP_THRUST_BEARING_VIBRATION)(\d+)(?:_PROCESS_VALUE|\.PV)?$/i,
    decimals: 2,
    chart: 'line'
  },
  {
    key: 'VIBRATION_SENSOR',
    label: () => t`Vibration sensor`,
    category: 'vibration',
    role: 'vibration',
    match: /^VIBRATION_SENSOR-?(\d+)$/i,
    decimals: 2,
    chart: 'line'
  },
  {
    key: 'VELOMETER',
    label: () => t`Motor velometer`,
    category: 'vibration',
    role: 'velometer',
    match: /^MOTOR_VIBRATION_VELOMETER-?(\d+)$/i,
    decimals: 2,
    chart: 'line'
  },

  // ------------------------------------------------------------------- cooling
  {
    key: 'COOLING_WATER_INLET',
    label: () => t`Cooling water inlet`,
    category: 'cooling',
    role: 'water_inlet',
    match:
      /^(?:PUMP_COOLING_WATER_INLET_TEMP|COOLING_WATER_INLET-?|PUMP_PUMP_INLET_COOLING_WATER_TEMPERATURED)(\d+)$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'COOLING_WATER_OUTLET_SIDE',
    label: () => t`Cooling water outlet`,
    category: 'cooling',
    role: 'water_outlet',
    match: /^PUMP_COOLING_WATER_COOL_OUTLET_(?<side>LEFT|RIGHT)D?\d*$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'COOLING_WATER_OUTLET',
    label: () => t`Cooling water outlet`,
    category: 'cooling',
    role: 'water_outlet',
    match:
      /^(?:PUMP_COOLING_WATER_OUTLET_TEMP|PUMP_OUTLET_COOLING_WATER_TEMP-?|PUMP_PUMP_OUTLET_COOLING_WATER_TEMP_)(\d+)(?:D\d+)?$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'COOLING_WATER_RTD',
    label: () => t`Cooling water RTD`,
    category: 'cooling',
    role: 'water',
    match: /^COOLING_WATER_RTD(\d+)$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'COLD_AIR_SIDE',
    label: () => t`Cooling air, cold side`,
    category: 'cooling',
    role: 'cold_air',
    match:
      /^PUMP_COOLING_AIR_(?<side>D_END_LEFT|D_END_RIGHT|ND_END_LEFT|ND_END_RIGHT)_COLDD\d+$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'COLD_AIR',
    label: () => t`Cold air`,
    category: 'cooling',
    role: 'cold_air',
    match: /^(?:PUMP_MOTOR_COLD_AIR ?TEMP|COLD_AIR_RTD)(\d+)$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'HOT_AIR',
    label: () => t`Hot air`,
    category: 'cooling',
    role: 'hot_air',
    match: /^(?:PUMP_MOTOR_HOT_AIR ?TEMP|HOT_AIR_RTD|HOT_AIR_OUTLET-?)(\d+)$/i,
    decimals: 1,
    chart: 'line'
  },

  // ------------------------------------------------------------------- bearing
  {
    key: 'THRUST_PAD',
    label: () => t`Thrust bearing pad`,
    category: 'bearing',
    role: 'thrust_pad',
    match:
      /^(?:THRST_BRG_THRST_PD_RTD|PUMP_THRUST_BEARING_PAD_TEMP)(\d+)(?:_PROCESS_VALUE)?$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'THRUST_AXIAL_PAD',
    label: () => t`Thrust axial pad`,
    category: 'bearing',
    role: 'thrust_pad',
    match: /^PUMP_THRUST_AXIAL_PAD_(\d+)D\d+$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'GUIDE_RADIAL_PAD',
    label: () => t`Guide radial pad`,
    category: 'bearing',
    role: 'guide_pad',
    match: /^PUMP_GUIDED_RADIAL_PAD_(\d+)D\d+$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'GUIDE_BEARING_PAD',
    label: () => t`Guide bearing pad`,
    category: 'bearing',
    role: 'guide_pad',
    match: /^PUMP_(?<side>UPPER|LOWER)_GUIDE_BEARING_PAD_TEMP(\d+)$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'GUIDE_BEARING_RTD',
    label: () => t`Guide bearing RTD`,
    category: 'bearing',
    role: 'guide_pad',
    match: /^GDE_BRNG_RTD(\d+)(?:_PROCESS_VALUE)?$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'BEARING_TEMP',
    label: () => t`Bearing`,
    category: 'bearing',
    role: 'bearing',
    match: /^PUMP_BEARING_TEMP(\d+)$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'OIL_RESERVOIR',
    label: () => t`Oil reservoir`,
    category: 'bearing',
    role: 'oil',
    match: /^PUMP_(?<side>UPPER|BOTTOM)_OIL_RESERVOR_TEMP(\d+)$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'GUIDE_BEARING_OIL',
    label: () => t`Guide bearing oil`,
    category: 'bearing',
    role: 'oil',
    match: /^PUMP_GUIDE_BEARING_OIL_TEMP(\d+)$/i,
    decimals: 1,
    chart: 'line'
  },
  {
    key: 'COOLER_OIL_OUTLET',
    label: () => t`Cooler oil outlet`,
    category: 'bearing',
    role: 'oil',
    match: /^(?:COOLING_OIL_OUTLET_TEMP|COOLER_OUTLET_OIL_TEMP)(\d+)$/i,
    decimals: 1,
    chart: 'line'
  },

  // ------------------------------------------------------------------- station
  {
    key: 'FOREBAY_LEVEL',
    label: () => t`Forebay level`,
    category: 'station',
    role: 'forebay',
    match: /^COMMAN_FORBAY_LEVEL$/i,
    decimals: 2,
    chart: 'area'
  },
  {
    key: 'SURGE_POOL_LEVEL',
    label: () => t`Surge pool level`,
    category: 'station',
    role: 'surge_pool',
    match: /^SURGEPOOL_LEVEL$/i,
    decimals: 2,
    chart: 'area'
  },
  {
    key: 'PUMPS_RUNNING',
    label: () => t`Pumps running`,
    category: 'station',
    role: 'pump_count',
    match: /^pc$/i,
    decimals: 0,
    chart: 'step'
  },
  {
    key: 'STATION_STATUS',
    label: () => t`Station status`,
    category: 'station',
    role: 'station_status',
    match: /^st$/i,
    decimals: 0,
    chart: 'step'
  }
];

/** The categories in the order the page presents them. */
export const CATEGORY_ORDER: readonly ParameterCategory[] = [
  'electrical',
  'operation',
  'hydraulic',
  'winding',
  'vibration',
  'cooling',
  'bearing',
  'station',
  'other'
];

export function categoryLabel(category: ParameterCategory): string {
  switch (category) {
    case 'electrical':
      return t`Electrical`;
    case 'operation':
      return t`Speed and operation`;
    case 'hydraulic':
      return t`Hydraulic and process`;
    case 'winding':
      return t`Motor winding temperature`;
    case 'vibration':
      return t`Vibration and mechanical`;
    case 'cooling':
      return t`Cooling and thermal`;
    case 'bearing':
      return t`Bearing and lubrication`;
    case 'station':
      return t`Station`;
    default:
      return t`Other signals`;
  }
}
