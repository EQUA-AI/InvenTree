import { describe, expect, it } from 'vitest';
import { faultDescription } from './faultPresentation';

describe('fault descriptions', () => {
  it('uses an API description or dictionary entry without site-specific rules', () => {
    expect(faultDescription('NORTH-P3-BRG', ' Bearing temperature high ')).toBe(
      'Bearing temperature high'
    );
    expect(
      faultDescription('NORTH-P3-BRG', null, {
        'NORTH-P3-BRG': { description: 'Bearing alarm' }
      })
    ).toBe('Bearing alarm');
  });
  it('leaves unfamiliar and Cedar Creek codes visible instead of inventing descriptions', () => {
    expect(faultDescription('NORTH-P3-BRG')).toBe('NORTH-P3-BRG');
    expect(faultDescription('CC-PUMP-LOW_FLOW', '')).toBe('CC-PUMP-LOW_FLOW');
  });
});
