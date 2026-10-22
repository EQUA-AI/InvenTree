import { describe, expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, i) => text + part + (args[i] ?? ''), '')
}));
import { signalReading } from './readingPresentation';

describe('signal readings retain supplied facts without inventing units', () => {
  it('keeps a measured zero with its supplied unit', () => {
    expect(signalReading({ value: 0, unit: 'm3/h' })).toEqual({
      text: '0 m³/h',
      reason: null,
      kind: 'value'
    });
  });
  it('keeps meaningful precision and explicit dimensionless units', () => {
    expect(signalReading({ value: 120.9724, unit: 'm' }).text).toBe(
      '120.972 m'
    );
    expect(signalReading({ value: 1, unit: '1' }).text).toBe('1');
  });
  it.each(['', '  '])(
    'keeps a supplied value visible without inventing a missing unit (%j)',
    (unit) => {
      expect(signalReading({ value: 1834, unit })).toEqual({
        text: '1834 (no unit)',
        reason: null,
        kind: 'value'
      });
    }
  );
  it('does not turn an absent or invalid observation into zero', () => {
    expect(signalReading({ value: null, unit: 'bar' })).toEqual({
      text: '—',
      reason: 'No observation available',
      kind: 'missing'
    });
    expect(signalReading({ value: Number.NaN, unit: 'bar' })).toEqual({
      text: '—',
      reason: 'Invalid observation value',
      kind: 'invalid'
    });
  });
});
