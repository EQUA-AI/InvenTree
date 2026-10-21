export interface ThresholdBoundary {
  y: number;
  direction?: 'low' | 'high';
  severity?: 'warning' | 'critical';
}
export interface ThresholdBand {
  from: number;
  to: number;
  severity: 'warning' | 'critical';
}

/** Only draw configured directional limits, clipped to this chart's actual scale. */
export function thresholdBands(
  boundaries: readonly ThresholdBoundary[],
  low: number,
  high: number
): ThresholdBand[] {
  if (!Number.isFinite(low) || !Number.isFinite(high) || low >= high) return [];
  const usable = boundaries.filter(
    (b) => Number.isFinite(b.y) && b.direction && b.severity
  );
  const edges = [
    ...new Set([
      low,
      high,
      ...usable.map((b) => b.y).filter((y) => y > low && y < high)
    ])
  ].sort((a, b) => a - b);
  return edges.slice(1).flatMap((to, i) => {
    const from = edges[i];
    const mid = (from + to) / 2;
    const crossed = usable.filter((b) =>
      b.direction === 'high' ? mid > b.y : mid < b.y
    );
    const severity = crossed.some((b) => b.severity === 'critical')
      ? 'critical'
      : crossed.some((b) => b.severity === 'warning')
        ? 'warning'
        : null;
    return severity ? [{ from, to, severity }] : [];
  });
}

/** Boundaries inside the displayed domain are the only ones claimed as drawn. */
export function splitLimits<T extends ThresholdBoundary>(
  lines: readonly T[],
  low: number,
  high: number
) {
  const usable = lines.filter((line) => Number.isFinite(line.y));
  return {
    drawn: usable.filter((line) => line.y >= low && line.y <= high),
    offScale: usable.filter((line) => line.y < low || line.y > high)
  };
}
