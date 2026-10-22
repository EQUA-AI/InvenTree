import type { SeriesResponse } from '@lib/types/MachineHealth';
import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, index) => text + part + (args[index] ?? ''), '')
}));
import type { KpiTile } from './KpiStrip';
import { LiveIndicator } from './TimeRangeControl';

const now = Date.parse('2026-10-22T09:00:00Z');
const response = {
  recorded_telemetry_as_live: true,
  display_shifted: true,
  display_shift_seconds: 86400,
  live: {
    enabled: true,
    reporting: true,
    last_observed_at: new Date(now).toISOString(),
    last_error_code: ''
  },
  limits: {},
  mode: 'complete',
  documents_read: 10,
  cadence_seconds: 5,
  series: [{ available: true }]
} as SeriesResponse;
const render = (source = response) =>
  renderToStaticMarkup(
    createElement(
      MantineProvider,
      {},
      createElement(LiveIndicator, {
        live: true,
        fetching: false,
        newestAt: now,
        updatedAt: now,
        response: source,
        freshnessSeconds: 60,
        now,
        tiles: [
          { stale: false, observedAt: now },
          { stale: true, observedAt: now + 5000 },
          { stale: true, observedAt: now - 10000 }
        ] as KpiTile[],
        controls: (state, disclosure) =>
          createElement(
            'button',
            { type: 'button', 'aria-label': disclosure },
            state
          )
      })
    )
  );
it('summarises the worst current tile state and retains the recorded-clock disclosure in the control name', () => {
  const markup = render();
  expect(markup).toContain('>Live · 2 stale</button>');
  expect(markup).toContain(
    'aria-label="Recorded source presented on the live clock; timestamps shifted by 24 h."'
  );
});
it('source read failure takes precedence over live or stale tiles', () => {
  const markup = render({
    ...response,
    live: { ...response.live, read_failed: true }
  });
  expect(markup).toContain('>Read failed</button>');
  expect(markup).not.toContain('>Live ·');
});
it('does not revive a resolved older error when the source explicitly reports a successful read', () => {
  const markup = render({
    ...response,
    live: {
      ...response.live,
      read_failed: false,
      last_error_code: 'OLD_RESOLVED_ERROR'
    }
  });
  expect(markup).toContain('>Live · 2 stale</button>');
  expect(markup).not.toContain('>Read failed</button>');
});
