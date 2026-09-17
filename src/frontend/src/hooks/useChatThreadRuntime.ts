import {
  type SetStateAction,
  useCallback,
  useMemo,
  useRef,
  useState
} from 'react';
import type { ChatMessage } from './UseAIChat';

export interface ChatThreadRuntime {
  messages: ChatMessage[];
  isLoading: boolean;
  error: string | null;
  beforeSequence: number | null;
  loadingEarlier: boolean;
}

export function emptyChatThreadRuntime(): ChatThreadRuntime {
  return {
    messages: [],
    isLoading: false,
    error: null,
    beforeSequence: null,
    loadingEarlier: false
  };
}

type RuntimeUpdate =
  | Partial<ChatThreadRuntime>
  | ((state: ChatThreadRuntime) => ChatThreadRuntime);

/** RAM only. Async setters retain their originating thread, never the selection. */
export function useChatThreadRuntime(
  threadId: string,
  isCurrent: () => boolean
) {
  const [threads, setThreads] = useState<Record<string, ChatThreadRuntime>>({});
  const retired = useRef(new Set<string>());
  const current = useRef(isCurrent);
  current.current = isCurrent;
  const patch = useCallback((id: string, update: RuntimeUpdate) => {
    if (!current.current() || retired.current.has(id)) return;
    setThreads((previous) => {
      if (!current.current() || retired.current.has(id)) return previous;
      const state = Object.hasOwn(previous, id)
        ? previous[id]
        : emptyChatThreadRuntime();
      return {
        ...previous,
        [id]:
          typeof update === 'function' ? update(state) : { ...state, ...update }
      };
    });
  }, []);
  const retire = useCallback((id: string) => {
    retired.current.add(id);
    setThreads((previous) => {
      const next = { ...previous };
      delete next[id];
      return next;
    });
  }, []);
  const setters = useMemo(() => {
    const field =
      <K extends keyof ChatThreadRuntime>(key: K) =>
      (update: SetStateAction<ChatThreadRuntime[K]>) =>
        patch(threadId, (state) => ({
          ...state,
          [key]:
            typeof update === 'function'
              ? (
                  update as (
                    previous: ChatThreadRuntime[K]
                  ) => ChatThreadRuntime[K]
                )(state[key])
              : update
        }));
    return {
      setMessages: field('messages'),
      setIsLoading: field('isLoading'),
      setError: field('error'),
      setBeforeSequence: field('beforeSequence'),
      setLoadingEarlier: field('loadingEarlier')
    };
  }, [threadId, patch]);
  return {
    ...(Object.hasOwn(threads, threadId)
      ? threads[threadId]
      : emptyChatThreadRuntime()),
    ...setters,
    patch,
    retire
  };
}
