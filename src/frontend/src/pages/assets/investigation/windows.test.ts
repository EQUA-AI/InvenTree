import { describe, expect, it, vi } from 'vitest';
import {
  MAX_HISTORY_WINDOW_MS,
  boundedWindow,
  displayLinkedWindow,
  investigationHref,
  linkedWindow,
  pickerInstant,
  sliceAt,
  sliceCount
} from './windows';
const hour = 3600_000;
describe('bounded linked historian windows', () => {
  it('converts recorded bounds once using a known non-zero display shift', () => {
    const params = new URLSearchParams(
      'history_from=2025-09-20T00:00:00Z&history_to=2025-09-20T01:00:00Z&history_clock=recorded'
    );
    expect(displayLinkedWindow(params, 365 * 86400).window).toEqual({
      from: Date.parse('2026-09-20T00:00:00Z'),
      to: Date.parse('2026-09-20T01:00:00Z')
    });
    expect(displayLinkedWindow(params).clockUnknown).toBe(true);
    expect(displayLinkedWindow(params).window).toBeNull();
    expect(displayLinkedWindow(params, 0).window).toEqual(
      linkedWindow(params).window
    );
    params.set('history_clock', 'unrecognized');
    expect(displayLinkedWindow(params, 0).window).toBeNull();
  });
  it('parses the Mantine picker format without the native Date string parser', () => {
    const parser = vi.spyOn(Date, 'parse').mockReturnValue(Number.NaN);
    try {
      expect(pickerInstant('2026-10-01 12:34:56')).toBe(
        new Date(2026, 9, 1, 12, 34, 56).getTime()
      );
      expect(pickerInstant(null)).toBeNaN();
    } finally {
      parser.mockRestore();
    }
  });
  it('retains the existing 24 hour limit including exact boundaries', () => {
    expect(boundedWindow(0, 24 * hour)).toEqual({ from: 0, to: 24 * hour });
    expect(boundedWindow(0, 24 * hour + 1)).toBeNull();
    expect(boundedWindow(4, 4)).toBeNull();
    expect(boundedWindow(Number.NaN, 5)).toBeNull();
    expect(boundedWindow(0, Number.POSITIVE_INFINITY)).toBeNull();
  });
  it('long case spans have contiguous explicit slices and an exact final edge', () => {
    const span = { from: 1234, to: 1234 + 73 * hour };
    expect(sliceCount(span)).toBe(4);
    const slices = [0, 1, 2, 3].map((i) => sliceAt(span, i)!);
    expect(slices.map((slice) => slice.to - slice.from)).toEqual([
      MAX_HISTORY_WINDOW_MS,
      MAX_HISTORY_WINDOW_MS,
      MAX_HISTORY_WINDOW_MS,
      hour
    ]);
    expect(slices[0].from).toBe(span.from);
    expect(slices[3].to).toBe(span.to);
    for (let i = 1; i < slices.length; i++)
      expect(slices[i].from).toBe(slices[i - 1].to);
  });
  it('does not create windows for a point snapshot or invalid slice index', () => {
    expect(sliceAt({ from: 1, to: 1 }, 0)).toBeNull();
    for (const index of [-1, 1, Number.NaN, 0.5])
      expect(sliceAt({ from: 1, to: 2 }, index)).toBeNull();
  });
  it('distinguishes an absent link from a malformed or excessive window', () => {
    expect(linkedWindow(new URLSearchParams())).toEqual({
      present: false,
      window: null
    });
    for (const query of [
      'history_from=x',
      'history_to=2026-01-01',
      'history_from=2026-01-01&history_to=2026-01-03'
    ])
      expect(linkedWindow(new URLSearchParams(query))).toEqual({
        present: true,
        window: null
      });
  });
  it('round trips bounded windows and encoded retained references without creating evidence', () => {
    const href = investigationHref(
      18,
      { from: hour, to: 2 * hour },
      901,
      'snapshot:/a+b'
    );
    const params = new URL(href, 'https://fixture.invalid').searchParams;
    expect(linkedWindow(params).window).toEqual({ from: hour, to: 2 * hour });
    expect(params.get('occurrence')).toBe('901');
    expect(params.get('snapshot')).toBe('snapshot:/a+b');
  });
});

describe('consumer-specific source limits', () => {
  it('Health can retain its 6 h trend bound without changing the 24 h series contract', () => {
    const span = { from: 0, to: 24 * hour };
    expect(sliceCount(span, 6 * hour)).toBe(4);
    expect(sliceAt(span, 3, 6 * hour)).toEqual({
      from: 18 * hour,
      to: 24 * hour
    });
    expect(sliceCount(span, 48 * hour)).toBe(1);
    expect(sliceCount(span, 0)).toBe(0);
  });
});
