/** RV10 contract: a MISSING required `focus` is rejected, an explicit
 * `focus: null` is a valid clear, and a legacy missing availability flag
 * stays deliberate. Regression for the release corrective pass. */
import { describe, expect, it } from 'vitest';

import {
  normalizeThreadFocusEcho,
  normalizeThreadFocusPayload
} from './conversationFocus';

describe('RV10: missing required focus is never normalized as an explicit clear', () => {
  it('rejects a thread focus payload whose focus property is missing', () => {
    expect(
      normalizeThreadFocusPayload({
        focus_version: 7,
        focus_unavailable: false
      })
    ).toBeNull();
    expect(normalizeThreadFocusPayload({ focus_version: 7 })).toBeNull();
    // A malformed focus is still rejected outright (unchanged control).
    expect(
      normalizeThreadFocusPayload({
        focus: { kind: 'nope', id: 1, label: 'x' },
        focus_version: 7,
        focus_unavailable: false
      })
    ).toBeNull();
  });

  it('accepts an explicit focus: null clear and a legacy missing availability flag', () => {
    expect(
      normalizeThreadFocusPayload({
        focus: null,
        focus_version: 7,
        focus_unavailable: false
      })
    ).toEqual({
      focus: null,
      focus_version: 7,
      focus_unavailable: false
    });
    // Legacy backend without the optional availability flag: default false.
    expect(
      normalizeThreadFocusPayload({ focus: null, focus_version: 7 })
    ).toEqual({ focus: null, focus_version: 7, focus_unavailable: false });
    expect(
      normalizeThreadFocusPayload({
        focus: { kind: 'machine', id: 3, label: 'Pump A' },
        focus_version: 7
      })
    ).toEqual({
      focus: {
        kind: 'machine',
        id: 3,
        label: 'Pump A',
        machine_id: null,
        work_order_id: null
      },
      focus_version: 7,
      focus_unavailable: false
    });
  });

  it('rejects a turn focus echo missing focus while explicit null echoes pass', () => {
    expect(
      normalizeThreadFocusEcho({
        focus_version: 2,
        focus_refresh_required: true
      })
    ).toBeNull();
    expect(
      normalizeThreadFocusEcho({
        focus: null,
        focus_version: 2,
        focus_refresh_required: false
      })
    ).toEqual({
      focus: null,
      focus_version: 2,
      focus_refresh_required: false
    });
  });
});
