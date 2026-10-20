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

import { openGlobalAIChat, useAIChatState } from './AIChatState';

describe('chat session boundary', () => {
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

  it('resets RAM for another tab without clearing its pending-deletion receipt', () => {
    useAIChatState.getState().open();
    useAIChatState.getState().resetSession(false);
    expect(mocks.clearIndices).not.toHaveBeenCalled();
    expect(mocks.removeQueries).toHaveBeenCalledOnce();
    expect(useAIChatState.getState()).toMatchObject({
      isOpen: false,
      sessionGeneration: 1
    });
  });

  it('invalidates async generations, clears navigation hints and discards private query results', () => {
    useAIChatState
      .getState()
      .openWithHint({ machineId: 7, machineName: 'Fixture pump' });
    useAIChatState.getState().bindHint('old-thread');
    const previousGeneration = useAIChatState.getState().sessionGeneration;
    useAIChatState.getState().resetSession();
    expect(useAIChatState.getState()).toMatchObject({
      isOpen: false,
      sessionGeneration: previousGeneration + 1,
      routingHint: undefined,
      hintThreadId: null
    });
    expect(mocks.clearIndices).toHaveBeenCalledOnce();
    const { predicate } = mocks.removeQueries.mock.calls[0][0];
    for (const key of [
      'chat-action-proposals',
      'ai-evidence-set',
      'voice-capability',
      'voice-operation',
      'approval-inbox',
      'approval-review',
      'approval-count',
      'mailboxes',
      'mailbox-messages',
      'mailbox-review',
      'mailbox-operation'
    ]) {
      expect(predicate({ queryKey: [key, 'old-account'] })).toBe(true);
    }
    expect(predicate({ queryKey: ['unrelated-theme'] })).toBe(false);
  });

  it('consumes a transient Chat-tab intent exactly once on machine Ask', () => {
    openGlobalAIChat({ machineId: 7, machineName: 'Fixture pump' });
    expect(useAIChatState.getState().chatOpenIntent).toBe('chat');
    useAIChatState.getState().consumeChatOpenIntent();
    expect(useAIChatState.getState().chatOpenIntent).toBeNull();
    // Consumed once: later renders must not keep forcing the Chat tab.
    useAIChatState.getState().consumeChatOpenIntent();
    expect(useAIChatState.getState().chatOpenIntent).toBeNull();
  });

  it('plain open keeps the persisted tab and close discards any intent', () => {
    useAIChatState.getState().open();
    expect(useAIChatState.getState().chatOpenIntent).toBeNull();
    useAIChatState
      .getState()
      .openWithHint({ machineId: 7, machineName: 'Fixture pump' });
    useAIChatState.getState().close();
    expect(useAIChatState.getState().chatOpenIntent).toBeNull();
  });

  it('resetting the session discards a stale open intent', () => {
    useAIChatState
      .getState()
      .openWithHint({ machineId: 7, machineName: 'Fixture pump' });
    useAIChatState.getState().resetSession(false);
    expect(useAIChatState.getState().chatOpenIntent).toBeNull();
  });
});

describe('generalized conversation target hint (plan C)', () => {
  beforeEach(() => {
    useAIChatState.setState({
      isOpen: false,
      sessionGeneration: 0,
      routingHint: undefined,
      targetHint: undefined,
      hintThreadId: null,
      chatOpenIntent: null
    });
  });

  it('maps the backward-compatible machine open onto a canonical target', () => {
    useAIChatState
      .getState()
      .openWithHint({ machineId: 7, machineName: 'Fixture pump' });
    expect(useAIChatState.getState().routingHint).toEqual({
      machineId: 7,
      machineName: 'Fixture pump'
    });
    // Same canonical identity, interim display label only.
    expect(useAIChatState.getState().targetHint).toEqual({
      kind: 'machine',
      id: 7,
      label: 'Fixture pump',
      machineId: 7
    });
  });

  it('opens with a board-card or work-order target without a machine scope seed', () => {
    openGlobalAIChat({
      kind: 'kanban_card',
      id: 11,
      label: 'Replace seal',
      workOrderId: 5
    });
    expect(useAIChatState.getState().targetHint).toEqual({
      kind: 'kanban_card',
      id: 11,
      label: 'Replace seal',
      workOrderId: 5
    });
    // No machine routing hint: no analysis-scope seeding for task targets.
    expect(useAIChatState.getState().routingHint).toBeUndefined();
    expect(useAIChatState.getState().chatOpenIntent).toBe('chat');

    openGlobalAIChat({ kind: 'work_order', id: 5, label: 'WO-000005' });
    expect(useAIChatState.getState().targetHint?.kind).toBe('work_order');
    expect(useAIChatState.getState().routingHint).toBeUndefined();
  });

  it('binds and clears the target hint together with the machine hint', () => {
    openGlobalAIChat({ kind: 'work_order', id: 5, label: 'WO-000005' });
    useAIChatState.getState().bindHint('thread-1');
    expect(useAIChatState.getState().hintThreadId).toBe('thread-1');
    useAIChatState.getState().clearHint();
    expect(useAIChatState.getState().targetHint).toBeUndefined();
    expect(useAIChatState.getState().hintThreadId).toBeNull();

    openGlobalAIChat({ kind: 'machine', id: 7, label: 'Pump A' });
    useAIChatState.getState().close();
    expect(useAIChatState.getState().targetHint).toBeUndefined();
    expect(useAIChatState.getState().routingHint).toBeUndefined();
    expect(useAIChatState.getState().chatOpenIntent).toBeNull();
  });
});
