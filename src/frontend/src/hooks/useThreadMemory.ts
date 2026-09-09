/**
 * M2 PR 9 (plan 8.6 items 3-4, GR-16): the owner-only thread memory
 * inspection surface.
 *
 * `fetchThreadMemory` / `correctThreadMemory` mirror `updateThreadScope`'s
 * typed-result idiom in UseAIChat: raw `fetch`, session cookies, CSRF on
 * the unsafe verb, and a closed result union so the modal never renders a
 * server error body. Corrections deliberately carry NO idempotency key —
 * the endpoint is versioned by `through_sequence` and a 409 means "reload
 * and look again", which is exactly what the modal does.
 *
 * `useThreadMemory` is the thin state machine the modal consumes.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type {
  ThreadMemoryCorrectionRequest,
  ThreadMemoryCorrectionResponse,
  ThreadMemoryResponse
} from '@lib/types/AimmsWire.generated';
import { csrfHeaders } from './UseAIChat';

export type ThreadMemoryFetchResult =
  | { ok: true; memory: ThreadMemoryResponse }
  | { ok: false; code: 'not_found' | 'error' };

export type ThreadMemoryCorrectionResult =
  | { ok: true; result: ThreadMemoryCorrectionResponse }
  | { ok: false; code: 'conflict' | 'invalid' | 'not_found' | 'error' };

export type ThreadMemoryCorrectionAction =
  ThreadMemoryCorrectionRequest['action'];

function memoryUrl(threadId: string, host: string): string {
  return `${host}/threads/${encodeURIComponent(threadId)}/memory`;
}

/**
 * GET the parsed summary body. A 404 is the documented "not available"
 * answer (non-owner, no server row, no summary yet) and is never an error.
 */
export async function fetchThreadMemory(
  threadId: string,
  host: string
): Promise<ThreadMemoryFetchResult> {
  try {
    const response = await fetch(memoryUrl(threadId, host), {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include'
    });
    if (response.ok) {
      return {
        ok: true,
        memory: (await response.json()) as ThreadMemoryResponse
      };
    }
    if (response.status === 404) {
      return { ok: false, code: 'not_found' };
    }
    return { ok: false, code: 'error' };
  } catch (error) {
    console.error('Error fetching thread memory:', error);
    return { ok: false, code: 'error' };
  }
}

/**
 * PUT one correction ("wrong" supersedes, "forget" excludes). The body is
 * exactly the generated `ThreadMemoryCorrectionRequest` — nothing else.
 */
export async function correctThreadMemory(
  threadId: string,
  host: string,
  itemId: string,
  action: ThreadMemoryCorrectionAction
): Promise<ThreadMemoryCorrectionResult> {
  const body: ThreadMemoryCorrectionRequest = { item_id: itemId, action };
  try {
    const response = await fetch(`${memoryUrl(threadId, host)}/corrections`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        ...csrfHeaders()
      },
      credentials: 'include',
      body: JSON.stringify(body)
    });
    if (response.ok) {
      return {
        ok: true,
        result: (await response.json()) as ThreadMemoryCorrectionResponse
      };
    }
    if (response.status === 409) {
      return { ok: false, code: 'conflict' };
    }
    if (response.status === 422) {
      return { ok: false, code: 'invalid' };
    }
    if (response.status === 404) {
      return { ok: false, code: 'not_found' };
    }
    return { ok: false, code: 'error' };
  } catch (error) {
    console.error('Error correcting thread memory:', error);
    return { ok: false, code: 'error' };
  }
}

export type ThreadMemoryStatus =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'not_found'
  | 'error';

/** Post-correction notice codes; the modal maps them to localized copy. */
export type ThreadMemoryNotice = 'conflict' | 'failed' | null;

export interface ThreadMemoryState {
  status: ThreadMemoryStatus;
  memory: ThreadMemoryResponse | null;
  notice: ThreadMemoryNotice;
  /** The item whose correction is in flight (buttons disable meanwhile). */
  busyItemId: string | null;
  reload: () => Promise<void>;
  correct: (
    itemId: string,
    action: ThreadMemoryCorrectionAction
  ) => Promise<void>;
}

/**
 * Load the memory body while `enabled` (the modal is open) and expose the
 * correction verbs. A stale response from a previous thread never lands:
 * every load carries a ticket and only the newest ticket may set state, and
 * every await (the GET, and the PUT before its refetch) is followed by a
 * check that the modal is still open for the SAME thread — a correction
 * whose PUT resolves after the modal closed must not refetch and repaint a
 * body that the next open (for another thread) would then inherit.
 */
export function useThreadMemory(
  threadId: string | null,
  host: string,
  enabled: boolean
): ThreadMemoryState {
  const [status, setStatus] = useState<ThreadMemoryStatus>('idle');
  const [memory, setMemory] = useState<ThreadMemoryResponse | null>(null);
  const [notice, setNotice] = useState<ThreadMemoryNotice>(null);
  const [busyItemId, setBusyItemId] = useState<string | null>(null);
  const ticketRef = useRef(0);
  /** The thread the modal is currently open for (null while closed). */
  const openRef = useRef<string | null>(null);

  const reload = useCallback(async () => {
    if (!threadId) {
      setStatus('idle');
      setMemory(null);
      return;
    }
    const ticket = ++ticketRef.current;
    setStatus('loading');
    const result = await fetchThreadMemory(threadId, host);
    if (ticket !== ticketRef.current || openRef.current !== threadId) return;
    if (result.ok) {
      setMemory(result.memory);
      setStatus('ready');
    } else {
      setMemory(null);
      setStatus(result.code === 'not_found' ? 'not_found' : 'error');
    }
  }, [threadId, host]);

  useEffect(() => {
    if (!enabled) {
      // Closing invalidates any in-flight load and clears the body so a
      // re-open for another thread never flashes the previous one.
      ticketRef.current += 1;
      openRef.current = null;
      setStatus('idle');
      setMemory(null);
      setNotice(null);
      setBusyItemId(null);
      return;
    }
    openRef.current = threadId;
    setNotice(null);
    // Belt and braces: never paint a previous body under this open, even
    // if some late write slipped past the guards above.
    setMemory(null);
    void reload();
  }, [enabled, threadId, reload]);

  const correct = useCallback(
    async (itemId: string, action: ThreadMemoryCorrectionAction) => {
      if (!threadId) return;
      setNotice(null);
      setBusyItemId(itemId);
      try {
        const result = await correctThreadMemory(
          threadId,
          host,
          itemId,
          action
        );
        // The modal closed (or moved to another thread) while the PUT was
        // in flight: the write stands server side, but nothing here may
        // refetch or repaint for a thread that is no longer open.
        if (openRef.current !== threadId) return;
        if (result.ok) {
          await reload();
          return;
        }
        if (result.code === 'conflict') {
          setNotice('conflict');
          await reload();
          return;
        }
        setNotice('failed');
      } finally {
        setBusyItemId(null);
      }
    },
    [threadId, host, reload]
  );

  return { status, memory, notice, busyItemId, reload, correct };
}
