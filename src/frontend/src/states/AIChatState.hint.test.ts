/** RV08: task/work-order (and machine) target hints bind and consume ONCE at
 * the acknowledgement/send boundary and can never re-apply on a later send,
 * another thread or another account. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  removeQueries: vi.fn(),
  clearIndices: vi.fn()
}));
vi.mock('../App', () => ({
  queryClient: { removeQueries: mocks.removeQueries }
}));
vi.mock('../functions/chatThreadCache', () => ({
  clearChatIndices: mocks.clearIndices
}));

import { useAIChatState } from './AIChatState';

describe('RV08: hints bind/consume exactly once at the send boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAIChatState.setState({
      isOpen: false,
      sessionGeneration: 0,
      routingHint: undefined,
      targetHint: undefined,
      hintThreadId: null,
      chatOpenIntent: null
    });
  });

  it('consumes a work-order target exactly once at its acknowledging thread', () => {
    useAIChatState
      .getState()
      .openWithTargetHint({ kind: 'work_order', id: 5, label: 'WO-000005' });
    useAIChatState.getState().bindHint('thread-1');
    useAIChatState.getState().consumeHint('thread-1');
    expect(useAIChatState.getState().targetHint).toBeUndefined();
    expect(useAIChatState.getState().hintThreadId).toBeNull();
    // A second send has no hint left to re-apply.
    useAIChatState.getState().consumeHint('thread-1');
    expect(useAIChatState.getState().targetHint).toBeUndefined();
  });

  it('never consumes a bound hint at another thread; account reset fences it', () => {
    useAIChatState.getState().openWithTargetHint({
      kind: 'kanban_card',
      id: 11,
      label: 'Replace seal'
    });
    useAIChatState.getState().bindHint('thread-1');
    useAIChatState.getState().consumeHint('thread-2');
    // Still pending for its OWN thread only — never applied/consumed elsewhere.
    expect(useAIChatState.getState().targetHint).toBeDefined();
    expect(useAIChatState.getState().hintThreadId).toBe('thread-1');
    useAIChatState.getState().consumeHint('thread-1');
    expect(useAIChatState.getState().targetHint).toBeUndefined();
    // Account/session reset: nothing survives to re-apply anywhere.
    useAIChatState
      .getState()
      .openWithTargetHint({ kind: 'work_order', id: 5, label: 'WO' });
    useAIChatState.getState().resetSession();
    expect(useAIChatState.getState().targetHint).toBeUndefined();
    expect(useAIChatState.getState().hintThreadId).toBeNull();
  });

  it('preserves machine Ask: routing and target hints consume together once', () => {
    useAIChatState
      .getState()
      .openWithHint({ machineId: 7, machineName: 'Fixture pump' });
    expect(useAIChatState.getState().routingHint).toBeDefined();
    expect(useAIChatState.getState().targetHint).toBeDefined();
    useAIChatState.getState().consumeHint('thread-1');
    expect(useAIChatState.getState().routingHint).toBeUndefined();
    expect(useAIChatState.getState().targetHint).toBeUndefined();
    expect(useAIChatState.getState().hintThreadId).toBeNull();
  });
});
