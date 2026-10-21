import type { MachineSignalLimits } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import type { ReferenceLine } from './TrendChart';
import { formatValue } from './format';

/** Configured mapping bounds only, shared by Health and Performance charts. */
export function limitLines(
  limits: Partial<MachineSignalLimits>,
  unit: string,
  decimals = 2,
  yAxisId: 'left' | 'right' = 'left'
): ReferenceLine[] {
  const lines: ReferenceLine[] = [];
  for (const [name, direction, severity] of [
    ['warn_min', 'low', 'warning'],
    ['warn_max', 'high', 'warning'],
    ['critical_min', 'low', 'critical'],
    ['critical_max', 'high', 'critical']
  ] as const) {
    const y = limits[name];
    if (y == null || !Number.isFinite(y)) continue;
    const value = formatValue(y, decimals, unit);
    const label =
      severity === 'warning'
        ? direction === 'low'
          ? t`Warning below ${value}`
          : t`Warning above ${value}`
        : direction === 'low'
          ? t`Critical below ${value}`
          : t`Critical above ${value}`;
    lines.push({
      y,
      direction,
      severity,
      yAxisId,
      label,
      color: severity === 'critical' ? 'red.9' : 'yellow.8'
    });
  }
  return lines;
}
