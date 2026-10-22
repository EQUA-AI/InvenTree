import { describe, expect, it } from 'vitest';
import {
  recordedTelemetryAsLive,
  showRecordedTelemetry
} from './telemetryPresentation';

describe('O27 presentation follows the backend switch', () => {
  it('defaults on and keeps shift metadata out of the visible presentation', () => {
    expect(recordedTelemetryAsLive()).toBe(true);
    expect(showRecordedTelemetry({ display_shifted: true })).toBe(false);
    expect(
      showRecordedTelemetry({
        display_shifted: true,
        recorded_telemetry_as_live: true
      })
    ).toBe(false);
  });
  it('restores O23 caveats only for shifted data with the switch off', () => {
    expect(
      showRecordedTelemetry({
        display_shifted: true,
        recorded_telemetry_as_live: false
      })
    ).toBe(true);
    expect(
      showRecordedTelemetry({
        display_shifted: false,
        recorded_telemetry_as_live: false
      })
    ).toBe(false);
  });
});
