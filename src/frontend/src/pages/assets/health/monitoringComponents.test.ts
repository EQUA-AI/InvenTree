import type { SeriesResponse } from '@lib/types/MachineHealth';
import {
  DEFAULT_THEME,
  MantineProvider,
  getContrastColor,
  parseThemeColor
} from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, i) => text + part + (args[i] ?? ''), ''),
  plural: (n: number, forms: { one: string; other: string }) =>
    (n === 1 ? forms.one : forms.other).replace('#', String(n))
}));
import { HistoryProvenance } from '../investigation/HistoryProvenance';
import { ThresholdKey } from '../performance/ThresholdKey';
import { LiveIndicator } from '../performance/TimeRangeControl';
import { WindowStatus } from '../performance/WindowStatus';
import { HealthSummaryPanel } from './HealthSummary';
import { SourcePollStatus } from './SourcePollStatus';
import { SourcePollingBadge } from './SourcePollingBadge';
import { StatusBadge, type StatusTone } from './StatusBadge';
import { HealthStateBadge } from './common';
import { FreshnessText, freshnessFacts } from './common';
import { DisplayClockContext, displayClock } from './mimic/observedTime';
const render = (child: ReturnType<typeof createElement>) =>
  renderToStaticMarkup(createElement(MantineProvider, {}, child));
const text = (html: string) =>
  html.replace(/<style[^>]*>[\s\S]*?<\/style>/g, '').replace(/<[^>]*>/g, '');
it('snapshots every tone against the native Badge output', () => {
  const tones: StatusTone[] = [
    'critical',
    'warning',
    'info',
    'normal',
    'neutral',
    'stale',
    'live'
  ];
  const output = tones.map((tone) => {
    const variant =
      tone === 'critical' || tone === 'warning' ? 'filled' : 'outline';
    const html = render(createElement(StatusBadge, { tone, variant }, tone));
    return `${tone}:${/data-variant="([^"]+)"/.exec(html)?.[1]}:${/tabler-icon-([a-z-]+)/.exec(html)?.[1] ?? 'none'}`;
  });
  expect(output).toMatchInlineSnapshot(`
    [
      "critical:filled:urgent",
      "warning:filled:alert-triangle",
      "info:outline:info-circle",
      "normal:outline:circle-check",
      "neutral:outline:none",
      "stale:outline:clock-exclamation",
      "live:outline:broadcast",
    ]
  `);
});
it('passes shade and CSS colours unchanged and lets callers set dimensions', () => {
  for (const color of ['red.8', '#123456', 'var(--mantine-primary-color-7)']) {
    const badge = StatusBadge({
      tone: 'critical',
      variant: 'filled',
      color,
      h: 44,
      mih: 44,
      children: 'Test'
    });
    expect(badge.props.color).toBe(color);
    expect(badge.props.h).toBe(44);
    expect(badge.props.mih).toBe(44);
    expect(badge.props.autoContrast).toBe(true);
  }
});
it('keeps filled status text readable in both native colour schemes', () => {
  for (const color of ['red', 'yellow', 'gray', 'green', 'blue']) {
    const badge = StatusBadge({
      tone: 'info',
      variant: 'filled',
      color,
      children: 'Status'
    });
    for (const colorScheme of ['light', 'dark'] as const) {
      const background = parseThemeColor({
        color: badge.props.color,
        theme: DEFAULT_THEME,
        colorScheme
      }).value;
      const channels = background
        .slice(1)
        .match(/../g)!
        .map((channel) => Number.parseInt(channel, 16) / 255)
        .map((channel) =>
          channel <= 0.04045
            ? channel / 12.92
            : ((channel + 0.055) / 1.055) ** 2.4
        );
      const luminance =
        channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
      const foreground = getContrastColor({
        color: badge.props.color,
        theme: DEFAULT_THEME,
        colorScheme,
        autoContrast: true
      });
      const contrast = foreground.includes('white')
        ? 1.05 / (luminance + 0.05)
        : (luminance + 0.05) / 0.05;
      expect(contrast, `${color} ${colorScheme}`).toBeGreaterThanOrEqual(4.5);
    }
  }
});
it('uses native body text for light and outline status variants', () => {
  for (const variant of ['light', 'outline']) {
    const badge = StatusBadge({
      tone: 'normal',
      variant,
      color: 'green',
      children: 'Running'
    });
    expect(badge.props.c).toBe('var(--mantine-color-text)');
  }
});
describe('FreshnessText uses the observation and server budget', () => {
  const now = Date.parse('2026-10-18T09:00:00Z');
  const show = (
    observedAt: string | number | null,
    stale = false,
    thresholdSeconds?: number | null
  ) =>
    text(
      render(
        createElement(FreshnessText, {
          observedAt,
          stale,
          thresholdSeconds,
          now
        })
      )
    );
  it('shows a full current instant and age', () =>
    expect(show('2026-10-18T08:59:40Z')).toContain(
      '18 Oct 2026 08:59:40 · 20 s old'
    ));
  it('shows stale age and the actual budget', () =>
    expect(show('2026-10-18T08:00:00Z', true, 300)).toContain(
      'Stale · 60 min old · freshness budget 300 s'
    ));
  it('keeps future observations explicit instead of reporting zero age', () =>
    expect(show('2026-10-18T12:00:00Z', true, 300)).toContain(
      'Stale (server flag) · 180 min ahead of your clock · freshness budget 300 s'
    ));
  it('names an absent budget without substituting zero', () =>
    expect(show('2026-10-18T08:00:00Z', true)).toContain(
      'budget not supplied'
    ));
  it('keeps a contradictory server stale flag explicit', () =>
    expect(show('2026-10-18T08:59:40Z', true, 300)).toContain(
      'Stale (server flag) · 20 s old'
    ));
  it('never labels an expired last-known observation just now under O27', () => {
    const output = text(
      render(
        createElement(FreshnessText, {
          observedAt: '2026-10-18T08:59:40Z',
          stale: true,
          thresholdSeconds: 10,
          now,
          preferJustNow: true
        })
      )
    );
    expect(output).toContain('Stale · 20 s old · freshness budget 10 s');
    expect(output).not.toContain('just now');
  });
  it('keeps absent and invalid observation times unavailable', () => {
    expect(show(null)).toBe('—');
    for (const value of [0, 'bad timestamp'])
      expect(show(value)).toBe('Read failed');
    const html = render(
      createElement(FreshnessText, { observedAt: null, stale: false, now })
    );
    expect(html).toContain(
      'aria-label="Observed: Observation time unavailable"'
    );
    expect(freshnessFacts(null, false, 300, now)).toBeNull();
  });
});

it('uses the inherited plant clock for health observations even when the browser is elsewhere', () => {
  const output = text(
    render(
      createElement(
        DisplayClockContext.Provider,
        { value: displayClock('Asia/Riyadh') },
        createElement(FreshnessText, {
          observedAt: '2026-10-18T08:59:40Z',
          stale: false,
          now: Date.parse('2026-10-18T09:00:00Z')
        })
      )
    )
  );
  expect(output).toContain('18 Oct 2026 11:59:40 · 20 s old');
});

it('keeps critical coverage inspectable without a duplicate prose alert or an empty count group', () => {
  const html = render(
    createElement(HealthSummaryPanel, {
      summary: {
        state: 'normal',
        last_observed_at: null,
        active_anomaly_count: 0,
        anomaly_counts: { critical: 0, warning: 0, info: 0 }
      } as any,
      signals: [],
      anomalies: []
    })
  );
  expect(text(html)).toContain('0 active anomalies');
  expect(html).toMatch(
    /<button[^>]*>[^<]*critical coverage unavailable<\/button>/
  );
  expect(html).not.toContain('role="alert"');
  expect(html).not.toContain('No active anomalies.');
});

it('labels unavailable anomaly counts and never replaces them with zero', () => {
  const output = text(
    render(
      createElement(HealthSummaryPanel, {
        summary: {
          state: 'unknown',
          last_observed_at: null,
          anomaly_counts: null
        } as any,
        signals: null,
        anomalies: null
      })
    )
  );
  expect(output).toContain('Active anomalies:—');
  expect(output).not.toContain('0 active anomalies');
});

it('labels an invalid summary time as a failed read without nesting tooltip buttons', () => {
  const html = render(
    createElement(HealthSummaryPanel, {
      summary: {
        state: 'normal',
        last_observed_at: 'invalid',
        active_anomaly_count: 0,
        anomaly_counts: { critical: 0, warning: 0, info: 0 }
      } as any,
      signals: [],
      anomalies: []
    })
  );
  expect(html).toContain('aria-label="Last observation: Read failed"');
  expect(html).not.toMatch(/<button\b[^>]*>(?:(?!<\/button>)[\s\S])*<button\b/);
  expect(text(html)).not.toContain('invalid');
});

it('keeps unknown poll results distinct from failed attempts and disabled sources', () => {
  const source = {
    active: true,
    healthy: false,
    last_success_at: null,
    last_error_at: null,
    last_error_code: ''
  } as any;
  expect(text(render(createElement(SourcePollStatus, { source })))).toContain(
    'Last poll result unavailable'
  );
  expect(
    text(
      render(
        createElement(SourcePollStatus, {
          source: { ...source, active: false }
        })
      )
    )
  ).toContain('Disabled');
  expect(
    text(
      render(
        createElement(SourcePollStatus, {
          source: {
            ...source,
            last_error_at: '2026-10-18T08:00:00Z',
            last_error_code: 'SOURCE_TIMEOUT'
          }
        })
      )
    )
  ).toContain('Failed 18 Oct 2026 08:00:00 · SOURCE_TIMEOUT');
  expect(
    text(
      render(
        createElement(SourcePollStatus, {
          source: {
            ...source,
            healthy: true,
            last_success_at: '2026-10-18T08:00:00Z'
          }
        })
      )
    )
  ).toContain('Succeeded 18 Oct 2026 08:00:00');
});
it('a partial Normal headline has its own tone while server breaches remain filled', () => {
  const partial = HealthStateBadge({ state: 'normal', neutralNormal: true });
  expect(partial.props.tone).toBe('partial');
  expect(partial.props.color).toBe('gray');
  for (const state of ['critical', 'warning'] as const) {
    const badge = HealthStateBadge({ state });
    expect(badge.props.variant).toBe('filled');
    expect(badge.props.tone).toBe(state);
  }
});

it('distinguishes exact partial, full and unknown assessment coverage without deriving counts from freshness', () => {
  const partial = HealthStateBadge({
    state: 'normal',
    coverage: { evaluated: 2, mapped: 6 }
  });
  expect(partial.props.tone).toBe('partial');
  expect(partial.props.children).toBe('Normal · 2/6');
  const full = HealthStateBadge({
    state: 'normal',
    coverage: { evaluated: 6, mapped: 6 }
  });
  expect(full.props.tone).toBe('normal');
  expect(full.props.children).toBe('Normal');
  const unknown = HealthStateBadge({ state: 'normal', coverageUnknown: true });
  expect(unknown.props.tone).toBe('partial');
  expect(unknown.props.children).toBe('Normal · coverage unknown');
});

it('does not turn an absent history cadence into zero seconds', () => {
  const response = {
    mode: 'complete',
    documents_read: 3,
    cadence_seconds: null,
    limits: { expected_sample_interval_seconds: null },
    live: { enabled: true, last_poll_at: null, poll_interval_seconds: null }
  } as any;
  const html = text(
    render(
      createElement(LiveIndicator, {
        live: false,
        fetching: false,
        newestAt: null,
        updatedAt: null,
        response,
        freshnessSeconds: null,
        now: 1
      })
    )
  );
  expect(html).toContain('cadence not supplied');
  expect(html).not.toContain('cadence 0');
});

describe('one history read retains its metadata alongside the live summary', () => {
  const show = (
    expectedInterval: number | null | undefined,
    lastPollAt: string | null
  ) => {
    // Missing server metadata is handled defensively even though the API type
    // currently declares the expected interval as required.
    const response = {
      machine: 1,
      station: null,
      window_start: '2026-10-20T23:00:00Z',
      window_end: '2026-10-21T00:00:00Z',
      window_seconds: 3600,
      mode: 'complete',
      slots: null,
      resolution_seconds: 5,
      cadence_seconds: 5,
      expected_documents: 3,
      documents_read: 3,
      display_shifted: false,
      display_shift_seconds: 0,
      live: {
        enabled: true,
        last_poll_at: lastPollAt,
        last_error_code: '',
        poll_interval_seconds: 30
      },
      limits: {
        max_window_seconds: 86400,
        complete_read_max_documents: 10000,
        min_points: 2,
        max_points: 4320,
        expected_sample_interval_seconds: expectedInterval
      },
      series: []
    } as SeriesResponse;
    return text(
      render(
        createElement(
          DisplayClockContext.Provider,
          { value: displayClock('Asia/Riyadh') },
          createElement(LiveIndicator, {
            live: false,
            fetching: false,
            newestAt: null,
            updatedAt: null,
            response,
            freshnessSeconds: null,
            now: Date.parse(response.window_end)
          }),
          createElement(HistoryProvenance, {
            response,
            loading: false,
            updating: false,
            failed: false,
            extent: undefined,
            onRetry: vi.fn()
          })
        )
      )
    );
  };

  it('retains each metadata fact once and uses the plant clock for poll and window times', () => {
    const output = show(30, '2026-10-20T23:59:58Z');
    expect(output).toContain(
      'Every reading in the window (3 snapshots, cadence 5 s)'
    );
    expect(
      output.match(/Expected sample interval 30 s \(server default\)/g)
    ).toHaveLength(1);
    expect(
      output.match(/last poll attempt 21 Oct 2026 02:59:58/g)
    ).toHaveLength(1);
    expect(output).toContain(
      'Window: 21 Oct 2026 02:00:00 — 21 Oct 2026 03:00:00'
    );
    expect(output).not.toContain('last poll attempt 20 Oct 2026 23:59:58');
  });

  it.each([null, undefined])(
    'keeps a missing expected interval (%s) unknown and omits an absent poll',
    (interval) => {
      const output = show(interval, null);
      expect(output.match(/Expected interval not supplied/g)).toHaveLength(1);
      expect(output).not.toContain('Expected sample interval');
      expect(output).not.toContain('last poll attempt');
    }
  );

  it('preserves a supplied zero interval and labels an invalid poll time unavailable', () => {
    const output = show(0, 'invalid timestamp');
    expect(
      output.match(/Expected sample interval 0 s \(server default\)/g)
    ).toHaveLength(1);
    expect(output.match(/last poll attempt Time unavailable/g)).toHaveLength(1);
    expect(output).not.toContain('invalid timestamp');
  });
});

it('keeps one silent window status without a response and none in comparison children', () => {
  const props = {
    response: undefined,
    updating: false,
    now: Date.parse('2026-10-18T09:00:00Z'),
    showClock: false
  };
  const html = render(createElement(WindowStatus, props));
  expect(html.match(/<output\b/g)).toHaveLength(1);
  expect(text(html)).toBe('');
  expect(
    render(createElement(WindowStatus, { ...props, announce: false }))
  ).not.toContain('<output');
});

describe('O27 current presentation and O23 fallback', () => {
  const response = {
    recorded_telemetry_as_live: true,
    display_shifted: true,
    display_shift_seconds: 400 * 86400,
    mode: 'sampled',
    resolution_seconds: 60,
    documents_read: 3,
    expected_documents: 3,
    limits: {},
    live: { enabled: true, last_error_code: '', poll_interval_seconds: null }
  } as any;
  const indicator = (recorded_telemetry_as_live: boolean) =>
    text(
      render(
        createElement(LiveIndicator, {
          live: true,
          fetching: false,
          newestAt: 1000,
          updatedAt: 1000,
          response: { ...response, recorded_telemetry_as_live },
          freshnessSeconds: 300,
          now: 1000
        })
      )
    );
  it('shows the shifted newest reading as live and just now without clock caveats', () => {
    expect(indicator(true)).toContain('newest reading just now');
    expect(indicator(true)).not.toContain('Source history display shift');
  });
  it('keeps the existing O23 clock caveat when off', () => {
    expect(indicator(false)).toContain(
      'Source history display shift: 400 d later than recorded'
    );
  });
  it('judges source reporting by the shifted reading while retaining failures', () => {
    const source = {
      active: true,
      healthy: true,
      recorded_telemetry_as_live: true,
      reporting: true,
      last_observed_at: '2026-10-21T00:00:00Z',
      last_success_at: '2025-08-01T00:00:00Z',
      last_error_code: '',
      freshness_threshold_seconds: 300
    } as any;
    const show = (extra: object) =>
      text(
        render(
          createElement(SourcePollStatus, { source: { ...source, ...extra } })
        )
      );
    expect(show({})).toContain('Source reporting');
    expect(show({})).not.toContain('Succeeded 1 Aug 2025');
    expect(show({ recorded_telemetry_as_live: false })).toContain(
      'Succeeded 1 Aug 2025'
    );
    expect(
      show({
        healthy: false,
        reporting: false,
        read_failed: false,
        last_error_code: 'OLD_RESOLVED_ERROR'
      })
    ).toContain('No current reading');
    expect(
      show({
        healthy: false,
        reporting: false,
        last_error_code: 'SOURCE_TIMEOUT',
        last_error_at: '2026-10-21T00:00:00Z'
      })
    ).toContain('Failed 21 Oct 2026');
  });
});

it('O27 sampled windows use current source freshness and retain unavailable and failed states', () => {
  const now = Date.parse('2026-10-21T00:00:00Z');
  const response = {
    recorded_telemetry_as_live: true,
    display_shifted: true,
    display_shift_seconds: 400 * 86400,
    mode: 'sampled',
    resolution_seconds: 10800,
    expected_documents: 1000,
    documents_read: 240,
    limits: {},
    series: [{ available: true }],
    live: {
      enabled: true,
      reporting: true,
      last_observed_at: new Date(now).toISOString(),
      last_error_code: '',
      poll_interval_seconds: 60
    }
  } as any;
  const show = (
    extra: object = {},
    live: object = {},
    sampleTime: number | null = now - 10800_000
  ) =>
    text(
      render(
        createElement(LiveIndicator, {
          live: true,
          fetching: false,
          newestAt: sampleTime,
          updatedAt: now,
          response: {
            ...response,
            ...extra,
            live: { ...response.live, ...live }
          },
          freshnessSeconds: 300,
          now
        })
      )
    );
  expect(show()).toContain('newest reading just now');
  expect(show()).toContain('latest chart sample');
  expect(show()).not.toContain('Stale');
  expect(show({}, { reporting: false })).toContain('Source unavailable');
  expect(
    show({}, { last_error_code: 'SOURCE_TIMEOUT', reporting: false })
  ).toContain('Source read failed');
  expect(show({ series: [{ available: false }] })).toContain(
    'Readings unavailable'
  );
  expect(
    show({ series: [{ available: true, samples: [] }] }, {}, null)
  ).toContain('No readings');
  expect(
    show({ series: [{ available: true, samples: [] }] }, {}, null)
  ).not.toContain('newest reading just now');
  expect(show({ recorded_telemetry_as_live: false })).toContain('Stale');
});

it.each([true, false])(
  'O27 threshold copy follows the source policy (%s) and retains limit uncertainty',
  (asLive) => {
    const output = text(
      render(
        createElement(ThresholdKey, {
          presentation: { recorded_telemetry_as_live: asLive },
          lines: [{ y: 50, label: 'Warning above 50 bar', color: 'yellow' }],
          unit: 'bar',
          low: 0,
          high: 100,
          include: false,
          onInclude: vi.fn(),
          hasOffScale: false
        })
      )
    );
    expect(output).toContain(
      asLive
        ? 'Engineering approval and limit applicability unavailable.'
        : 'Engineering approval and historical applicability unavailable.'
    );
    if (asLive) expect(output).not.toContain('historical');
  }
);

it.each([true, false])(
  'scheduler status uses the policy clock when telemetry is effectively enabled (%s)',
  (asLive) => {
    const output = text(
      render(
        createElement(SourcePollingBadge, {
          enabled: true,
          polling_enabled: false,
          recorded_telemetry_as_live: asLive
        })
      )
    );
    expect(output).toBe(asLive ? 'Polling paused' : 'Polling enabled');
  }
);
it('keeps the original off-state effective source status when the scheduler is enabled', () => {
  expect(
    text(
      render(
        createElement(SourcePollingBadge, {
          enabled: false,
          polling_enabled: true,
          recorded_telemetry_as_live: false
        })
      )
    )
  ).toBe('Polling paused');
});
it('keeps legacy polling status when the explicit scheduler field is absent', () => {
  expect(
    text(render(createElement(SourcePollingBadge, { enabled: true })))
  ).toBe('Polling enabled');
});
