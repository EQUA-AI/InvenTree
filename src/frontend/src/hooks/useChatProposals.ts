import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect } from 'react';
import { api } from '../App';
import type { ChatActionProposalPayload } from '../components/ai/ChatActionProposals';
import { useVoiceDecisionState } from '../states/VoiceDecisionState';

const key = ['chat-action-proposals'];

/** One drawer-level poller, shared by all tab presentations. */
export function useChatProposals() {
  const queryClient = useQueryClient();
  const sessionId = useVoiceDecisionState((state) => state.sessionId);
  const query = useQuery({
    queryKey: key,
    queryFn: async () => {
      await useVoiceDecisionState
        .getState()
        .refresh()
        .catch(() => {});
      return (await api.get('/api/aichat/proposals/')).data
        .results as ChatActionProposalPayload[];
    },
    refetchInterval: sessionId ? 5000 : 30000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true
  });
  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: key });
  }, [queryClient]);
  useEffect(() => {
    window.addEventListener('aimms:proposals-refresh', refresh);
    window.addEventListener('online', refresh);
    const visible = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    document.addEventListener('visibilitychange', visible);
    refresh();
    return () => {
      window.removeEventListener('aimms:proposals-refresh', refresh);
      window.removeEventListener('online', refresh);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [refresh, sessionId]);
  return { proposals: query.data ?? [], refresh };
}
