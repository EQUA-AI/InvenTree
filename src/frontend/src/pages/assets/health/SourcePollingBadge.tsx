import { t } from '@lingui/core/macro';
import { Badge } from '@mantine/core';
import { recordedTelemetryAsLive } from '../telemetryPresentation';

/** Scheduler state is independent of O27's effective telemetry availability. */
export function SourcePollingBadge({
  enabled,
  polling_enabled,
  recorded_telemetry_as_live
}: Readonly<{
  enabled: boolean;
  polling_enabled?: boolean;
  recorded_telemetry_as_live?: boolean;
}>) {
  const polling = recordedTelemetryAsLive({ recorded_telemetry_as_live })
    ? (polling_enabled ?? enabled)
    : enabled;
  return (
    <Badge color={polling ? 'blue' : 'yellow'}>
      {polling ? t`Polling enabled` : t`Polling paused`}
    </Badge>
  );
}
