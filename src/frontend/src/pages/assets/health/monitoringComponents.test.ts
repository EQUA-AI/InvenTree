import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, i) => text + part + (args[i] ?? ''), ''),
  plural: (n: number, forms: { one: string; other: string }) =>
    (n === 1 ? forms.one : forms.other).replace('#', String(n))
}));
import { LiveIndicator } from '../performance/TimeRangeControl';
import { WindowStatus } from '../performance/WindowStatus';
import { SourcePollStatus } from './SourcePollStatus';
import { StatusBadge, type StatusTone } from './StatusBadge';
import { HealthStateBadge } from './common';
import { FreshnessText, freshnessFacts } from './common';
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
  it('keeps absent and invalid observation times unavailable', () => {
    for (const value of [null, 0, 'bad timestamp'])
      expect(show(value)).toBe('Observation time unavailable');
    expect(freshnessFacts(null, false, 300, now)).toBeNull();
  });
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
it('a partial Normal headline is neutral while server breaches remain filled', () => {
  const partial = HealthStateBadge({ state: 'normal', neutralNormal: true });
  expect(partial.props.tone).toBe('neutral');
  expect(partial.props.color).toBe('gray');
  for (const state of ['critical', 'warning'] as const) {
    const badge = HealthStateBadge({ state });
    expect(badge.props.variant).toBe('filled');
    expect(badge.props.tone).toBe(state);
  }
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
