/** The backend owns the single O27 switch; absence follows its default. */
export interface TelemetryPresentation {
  recorded_telemetry_as_live?: boolean;
}

export function recordedTelemetryAsLive(value?: TelemetryPresentation) {
  return value?.recorded_telemetry_as_live !== false;
}

/** Keep the API's clock metadata while applying the owner's presentation. */
export function showRecordedTelemetry(
  value?: TelemetryPresentation & { display_shifted?: boolean }
) {
  return !!value?.display_shifted && !recordedTelemetryAsLive(value);
}

export const LIVE_TELEMETRY_WINDOW_SECONDS = 30 * 24 * 3600;
