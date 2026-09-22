import { useDocumentVisibility } from '@mantine/hooks';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo } from 'react';
import { api } from '../App';
import type { ChatActionProposalPayload } from '../components/ai/ChatActionProposals';
import {
  InvalidReadResponse,
  readPollInterval,
  readQueryPolicy
} from '../functions/readQueryPolicy';
import { useAIChatState } from '../states/AIChatState';
import { useLocalState } from '../states/LocalState';
import { useUserState } from '../states/UserState';
import { useVoiceDecisionState } from '../states/VoiceDecisionState';

/** One drawer-level poller, shared by all tab presentations. */
export function useChatProposals(opened: boolean) {
  const queryClient = useQueryClient();
  const host = useLocalState((state) => state.getHost());
  const userId = useUserState((state) =>
    state.isLoggedIn() ? state.userId() : undefined
  );
  const generation = useAIChatState((state) => state.sessionGeneration);
  const visibility = useDocumentVisibility();
  const sessionId = useVoiceDecisionState((state) => state.sessionId);
  const enabled = Boolean(
    userId && (opened || sessionId) && visibility === 'visible'
  );
  const key = useMemo(
    () => ['chat-action-proposals', host, userId, generation],
    [host, userId, generation]
  );
  const query = useQuery({
    ...readQueryPolicy,
    queryKey: key,
    enabled,
    queryFn: async ({ signal }) => {
      await useVoiceDecisionState
        .getState()
        .refresh(signal)
        .catch(() => {});
      const { data } = await api.get('/api/aichat/proposals/', {
        baseURL: host,
        signal
      });
      if (
        !Array.isArray(data?.results) ||
        !data.results.every(
          (row: ChatActionProposalPayload) =>
            row &&
            typeof row.id === 'string' &&
            typeof row.state === 'string' &&
            typeof row.action_type === 'string' &&
            row.preview &&
            typeof row.preview === 'object'
        )
      )
        throw new InvalidReadResponse('Invalid proposals response');
      return data.results as ChatActionProposalPayload[];
    },
    refetchInterval: (query) =>
      readPollInterval(query, sessionId ? 5000 : 30000),
    refetchIntervalInBackground: false
  });
  const refresh = useCallback(() => {
    if (!enabled) return;
    void queryClient.invalidateQueries({ queryKey: key });
  }, [queryClient, key, enabled]);
  useEffect(() => {
    if (!enabled) return;
    window.addEventListener('aimms:proposals-refresh', refresh);
    return () => {
      window.removeEventListener('aimms:proposals-refresh', refresh);
    };
  }, [enabled, refresh]);
  // Never keep actionable proposals visible after a failed revalidation.
  return {
    proposals: enabled && !query.isError ? (query.data ?? []) : [],
    refresh,
    error: query.error,
    loading: enabled && query.isLoading
  };
}
