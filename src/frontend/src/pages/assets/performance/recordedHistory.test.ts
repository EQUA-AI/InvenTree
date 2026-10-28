import type { MachineSignal } from '@lib/types/MachineHealth';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const captured = vi.hoisted(() => ({
  queries: [] as any[],
  get: vi.fn()
}));
vi.mock('@tanstack/react-query', () => ({
  useQuery: (options: unknown) => {
    captured.queries.push(options);
    return {};
  }
}));
vi.mock('../../../contexts/ApiContext', () => ({
  useApi: () => ({ get: captured.get })
}));
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, index) => text + part + (args[index] ?? ''), '')
}));

import {
  historySourceQueryKey,
  recordedHistorySource,
  recordedHistoryWindow
} from './recordedHistory';
import { resolveParameters } from './resolve';
import {
  PerformanceHistorySourceContext,
  useDataRange,
  useMachineSeries,
  useMachineSignals
} from './usePerformanceData';

const from = '2024-02-03T04:00:00.000Z';
const to = '2024-02-03T10:00:00.000Z';
const window = {
  kind: 'absolute' as const,
  from: Date.parse(from),
  to: Date.parse(to)
};

function Probe() {
  useDataRange(102);
  useMachineSignals(102, true);
  useMachineSeries(102, { bindings: [9] }, window, true);
  useMachineSignals(102, true, window);
  return null;
}

function render(sourceId?: number) {
  renderToStaticMarkup(
    createElement(
      PerformanceHistorySourceContext.Provider,
      { value: sourceId },
      createElement(Probe)
    )
  );
}

beforeEach(() => {
  captured.queries.length = 0;
  captured.get.mockReset();
  captured.get.mockImplementation(async (url: string) => ({
    data: url.includes('/series/')
      ? {
          machine: 102,
          window_start: from,
          window_end: to,
          display_shift_seconds: 0,
          resolution_seconds: 120,
          series: []
        }
      : { results: [], snapshot_at: to }
  }));
});

describe('explicit recorded Cosmos Performance', () => {
  it('requires an unambiguous positive safe source ID', () => {
    expect(recordedHistorySource('1')).toBe(1);
    for (const value of [
      null,
      '',
      '0',
      '-1',
      '1.2',
      '01',
      '1e3',
      ' 1',
      '9007199254740992'
    ]) {
      expect(recordedHistorySource(value)).toBeUndefined();
    }
  });

  it('anchors six hours to the actual source extent and waits for that extent', () => {
    const relative = { kind: 'relative' as const, seconds: 6 * 3600 };
    expect(recordedHistoryWindow(relative, to)).toEqual(window);
    expect(recordedHistoryWindow(relative, undefined)).toBeUndefined();
    expect(recordedHistoryWindow(relative, 'invalid')).toBeUndefined();
    expect(recordedHistoryWindow(window, undefined)).toBe(window);
  });

  it('keeps current cache identity and isolates every explicit source', () => {
    const current = ['machine-health-signals', 102];
    expect(historySourceQueryKey(current, undefined)).toBe(current);
    expect(historySourceQueryKey(current, 1)).not.toEqual(current);
    expect(historySourceQueryKey(current, 1)).not.toEqual(
      historySourceQueryKey(current, 2)
    );
  });

  it('leaves all default requests and polling untouched', async () => {
    render();
    await Promise.all(captured.queries.map((query) => query.queryFn({})));
    expect(captured.queries[0].queryKey).toEqual([
      'machine-health-data-range',
      102
    ]);
    expect(captured.queries[1].queryKey).toEqual([
      'machine-health-signals',
      102
    ]);
    expect(
      captured.get.mock.calls.every(
        ([, config]) => !config?.params?.history_source
      )
    ).toBe(true);
    expect(
      captured.get.mock.calls.find(([url]) => url.includes('/signals/'))?.[1]
    ).toBeUndefined();
    expect(captured.queries[1].refetchInterval({ state: {} })).toBe(15000);
  });

  it('scopes all data, history annotations and cache keys to the selected source without live polling', async () => {
    render(1);
    await Promise.all(captured.queries.map((query) => query.queryFn({})));
    expect(captured.get).toHaveBeenCalledTimes(4);
    for (const [, config] of captured.get.mock.calls) {
      expect(config.params.history_source).toBe('1');
    }
    for (const query of captured.queries) {
      expect(query.queryKey.slice(-2)).toEqual(['history-source', 1]);
      expect(
        typeof query.refetchInterval === 'function'
          ? query.refetchInterval({ state: {} })
          : query.refetchInterval
      ).toBe(false);
    }
    const seriesRequest = captured.get.mock.calls.find(([url]) =>
      url.includes('/series/')
    );
    expect(seriesRequest?.[1].params).toMatchObject({ from, to });
    const previous = { series: ['source 2'] };
    expect(
      captured.queries[2].placeholderData(previous, {
        queryKey: [...captured.queries[2].queryKey.slice(0, -1), 2]
      })
    ).toBeUndefined();
    expect(
      captured.queries[2].placeholderData(previous, {
        queryKey: captured.queries[2].queryKey
      })
    ).toBe(previous);
  });

  it('retains recorded snapshots and inactive mappings while excluding current-source TEST annotations', async () => {
    const signal = {
      binding_id: 9,
      source_id: 1,
      external_key: '/dex/PUMP8_ACTIVE_POWER',
      active: false,
      test_alarms: [{ anomaly_id: 12 }]
    } as MachineSignal;
    captured.get.mockResolvedValue({
      data: {
        results: [signal, { ...signal, binding_id: 10, source_id: 2 }],
        snapshot_at: to
      }
    });
    render(1);
    const signals = await captured.queries[1].queryFn({});
    expect(signals).toEqual([{ ...signal, snapshot_at: to, test_alarms: [] }]);
    expect(resolveParameters(signals)).toHaveLength(0);
    expect(
      resolveParameters(signals, true).map(
        (parameter) => parameter.signal.binding_id
      )
    ).toEqual([9]);
  });
});
