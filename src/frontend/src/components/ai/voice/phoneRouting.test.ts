import { describe, expect, it } from 'vitest';
import { classifyVoicePhone, stickyPhoneClassifier } from './phoneRouting';

const phone = {
  limit: 600,
  screenWidth: 393,
  screenHeight: 851,
  touchPoints: 5,
  coarsePointer: true
};

describe('experimental phone routing', () => {
  it('includes touch-first phones in either orientation at the approved boundary', () => {
    expect(classifyVoicePhone(phone)).toBe(true);
    expect(
      classifyVoicePhone({ ...phone, screenWidth: 851, screenHeight: 393 })
    ).toBe(true);
    expect(classifyVoicePhone({ ...phone, screenWidth: 600 })).toBe(true);
  });
  it('keeps tablets and narrow mouse-first desktops in the full app', () => {
    expect(classifyVoicePhone({ ...phone, screenWidth: 601 })).toBe(false);
    expect(
      classifyVoicePhone({ ...phone, screenWidth: 768, screenHeight: 1024 })
    ).toBe(false);
    expect(classifyVoicePhone({ ...phone, coarsePointer: false })).toBe(false);
    expect(classifyVoicePhone({ ...phone, touchPoints: 0 })).toBe(false);
  });
  it('defaults off and rejects malformed or unsupported policy values', () => {
    for (const limit of [undefined, null, 0, -1, '600', 600.5, 601, Number.NaN])
      expect(classifyVoicePhone({ ...phone, limit })).toBe(false);
    expect(classifyVoicePhone({ ...phone, screenWidth: 0 })).toBe(false);
  });
  it('never reclassifies during rotation, keyboard resize or device changes', () => {
    let input = { ...phone };
    const classify = stickyPhoneClassifier(() => input);
    expect(classify()).toBe(true);
    input = {
      ...input,
      screenWidth: 1024,
      screenHeight: 768,
      coarsePointer: false
    };
    expect(classify()).toBe(true);
    const tablet = stickyPhoneClassifier(() => input);
    expect(tablet()).toBe(false);
    input = { ...phone };
    expect(tablet()).toBe(false);
  });
});
