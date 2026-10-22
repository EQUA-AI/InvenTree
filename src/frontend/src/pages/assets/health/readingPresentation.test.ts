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
        text: '1834',
        reason: 'Unit not reported by the plant',
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

it('uses declared plant status labels and keeps unknown codes honest', () => {
  expect(
    signalReading({ value: 'I', unit: '', external_key: '/pd/P7/st' }).text
  ).toBe('Idle');
  expect(
    signalReading({ value: 'R', unit: '', external_key: '/pd/P7/st' }).text
  ).toBe('Running');
  expect(
    signalReading({ value: 'F', unit: '', external_key: '/pd/P7/st' }).text
  ).toBe('Unknown');
  expect(
    signalReading({
      value: 0,
      unit: '',
      external_key: '/dex/PUMP7_MOTOR_ON_OFF_STATUS'
    }).text
  ).toBe('Off');
});
it('uses two decimal places for forebay level without changing other measurements', () => {
  expect(
    signalReading({ value: 120.9724, unit: 'm', external_key: '/sl' }).text
  ).toBe('120.97 m');
});
