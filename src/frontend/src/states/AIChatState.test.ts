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

describe('chat session boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAIChatState.setState({
      isOpen: false,
      sessionGeneration: 0,
      routingHint: undefined,
      hintThreadId: null
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
});
