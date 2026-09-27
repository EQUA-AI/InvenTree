import { describe, expect, it } from 'vitest';

import {
  isNearBottom,
  pageContextKind,
  shouldFollowNewContent,
  shouldSendOnEnter,
  suggestionKinds
} from './chatComposer';

describe('composer Enter handling', () => {
  it('sends on plain Enter only', () => {
    expect(shouldSendOnEnter({ key: 'Enter', shiftKey: false })).toBe(true);
    expect(shouldSendOnEnter({ key: 'Enter', shiftKey: true })).toBe(false);
    expect(shouldSendOnEnter({ key: 'a', shiftKey: false })).toBe(false);
  });

  it('never sends mid-IME-composition', () => {
    expect(
      shouldSendOnEnter({ key: 'Enter', shiftKey: false, isComposing: true })
    ).toBe(false);
    // Legacy browsers surface composition via keyCode 229 without isComposing.
    expect(
      shouldSendOnEnter({ key: 'Enter', shiftKey: false, keyCode: 229 })
    ).toBe(false);
  });
});

describe('scroll follow behaviour', () => {
  const viewport = { scrollTop: 400, clientHeight: 300, scrollHeight: 1000 };

  it('treats the reader as near the bottom within the threshold', () => {
    expect(isNearBottom({ ...viewport, scrollTop: 719 }, 80)).toBe(true);
    expect(isNearBottom({ ...viewport, scrollTop: 700 }, 80)).toBe(true);
    expect(isNearBottom({ ...viewport, scrollTop: 600 }, 80)).toBe(false);
  });

  it('treats a non-scrollable transcript as pinned to the bottom', () => {
    expect(
      isNearBottom({ scrollTop: 0, clientHeight: 500, scrollHeight: 500 }, 80)
    ).toBe(true);
    expect(
      isNearBottom({ scrollTop: 0, clientHeight: 500, scrollHeight: 100 }, 80)
    ).toBe(true);
  });

  it('follows new content only for thread switches or an already-pinned reader', () => {
    expect(
      shouldFollowNewContent({
        threadChanged: true,
        anchoredPrepend: false,
        nearBottom: false
      })
    ).toBe(true);
    expect(
      shouldFollowNewContent({
        threadChanged: false,
        anchoredPrepend: true,
        nearBottom: true
      })
    ).toBe(false);
    expect(
      shouldFollowNewContent({
        threadChanged: false,
        anchoredPrepend: false,
        nearBottom: true
      })
    ).toBe(true);
    expect(
      shouldFollowNewContent({
        threadChanged: false,
        anchoredPrepend: false,
        nearBottom: false
      })
    ).toBe(false);
  });
});

describe('page context label', () => {
  it('maps known routes to friendly kinds and never returns the raw path', () => {
    expect(pageContextKind('/machines/index/sites/')).toBe('machines');
    expect(pageContextKind('/machine/5/health/')).toBe('machines');
    expect(pageContextKind('/maintenance/board/')).toBe('maintenance');
    expect(pageContextKind('/part/12/')).toBe('parts');
    expect(pageContextKind('/stock/item/3/')).toBe('stock');
    expect(pageContextKind('/order/purchase-order/9/')).toBe('orders');
    expect(pageContextKind('/home/')).toBe('home');
    expect(pageContextKind('/some/deeply/nested/unknown')).toBe('other');
  });
});

describe('empty-state suggestions', () => {
  it('offers machine-relevant prompts when a machine hint exists', () => {
    const kinds = suggestionKinds('Pump A');
    expect(kinds).toContain('machine-status');
    expect(kinds).toContain('machine-maintenance');
    expect(kinds.length).toBeGreaterThanOrEqual(2);
  });

  it('offers generic prompts without a machine hint', () => {
    const kinds = suggestionKinds(undefined);
    expect(kinds).not.toContain('machine-status');
    expect(kinds).not.toContain('machine-maintenance');
    expect(kinds).toContain('search-parts');
    expect(kinds).toContain('create-order');
    expect(kinds).toContain('low-stock');
  });
});
