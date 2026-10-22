import { t } from '@lingui/core/macro';
import { Stack, Switch } from '@mantine/core';
import { MetaText } from '../health/common';
import monitoring from '../monitoring.module.css';
import {
  type TelemetryPresentation,
  recordedTelemetryAsLive
} from '../telemetryPresentation';
import { useHistoryReadFailures } from './HistoryReadContext';
import type { ReferenceLine } from './TrendChart';
import { formatValue } from './format';
import { splitLimits, thresholdBands } from './thresholdBands';

export function ThresholdKey({
  lines,
  unit,
  low,
  high,
  include,
  onInclude,
  hasOffScale,
  presentation
}: Readonly<{
  lines: readonly ReferenceLine[];
  unit?: string;
  low: number;
  high: number;
  include: boolean;
  onInclude: (value: boolean) => void;
  hasOffScale: boolean;
  presentation?: TelemetryPresentation;
}>) {
  const history = useHistoryReadFailures();
  if (!lines.length) return null;
  const { drawn, offScale } = splitLimits(lines, low, high);
  const above = offScale
    .filter((line) => line.y > high)
    .map((line) => line.y - high);
  const below = offScale
    .filter((line) => line.y < low)
    .map((line) => low - line.y);
  const shaded = thresholdBands(lines, low, high).length > 0;
  return (
    <Stack gap={4} data-testid='threshold-key'>
      {(hasOffScale || include) && (
        <Switch
          label={t`Include limits in scale`}
          checked={include}
          onChange={(event) => onInclude(event.currentTarget.checked)}
          classNames={{ track: monitoring.track, thumb: monitoring.thumb }}
        />
      )}
      <MetaText size='xs'>
        {t`Configured binding limits (current Health signal mapping)`}.{' '}
        {drawn.length > 0
          ? t`Drawn: ${drawn.map((line) => line.label).join(' · ')}.`
          : t`Drawn: no configured boundaries inside this scale.`}{' '}
        {offScale.length > 0 &&
          t`Outside this scale: ${offScale.map((line) => line.label).join(' · ')}.`}{' '}
        {above.length > 0 &&
          below.length === 0 &&
          t`Window values are at least ${formatValue(Math.min(...above), 2, unit)} below the nearest limit.`}
        {below.length > 0 &&
          above.length === 0 &&
          t`Window values are at least ${formatValue(Math.min(...below), 2, unit)} above the nearest limit.`}{' '}
        {shaded && t`Shaded regions compare against these limits.`}{' '}
        {recordedTelemetryAsLive(presentation ?? history)
          ? t`Engineering approval and limit applicability unavailable.`
          : t`Engineering approval and historical applicability unavailable.`}
      </MetaText>
    </Stack>
  );
}
