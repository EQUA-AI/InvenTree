import { describe, expect, it } from 'vitest';
import { alarmFocusId } from './alarmFocus';

describe('alarm notification focus', () => {
  it('targets the immutable alarm id and rejects invalid identifiers', () => {
    expect(alarmFocusId('72')).toBe('machine-alarm-72');
    for (const value of [null, '', '0', '-2', '1.2', '72/health']) {
      expect(alarmFocusId(value)).toBeUndefined();
    }
  });
});
