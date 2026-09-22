import { useQuery } from '@tanstack/react-query';
import { useApi } from '../contexts/ApiContext';
import {
  InvalidReadResponse,
  readPollInterval,
  readQueryPolicy
} from '../functions/readQueryPolicy';
import { useAIChatState } from '../states/AIChatState';
import { useLocalState } from '../states/LocalState';
import { useUserState } from '../states/UserState';

interface UICapabilities {
  version: 1;
  risk_radar: boolean;
  command_center: boolean;
  maintenance_metrics: number;
  backend_commit: string;
}

export function useUICapabilities() {
  const api = useApi();
  const host = useLocalState((state) => state.getHost());
  const userId = useUserState((state) => state.user?.pk);
  const generation = useAIChatState((state) => state.sessionGeneration);
  return useQuery<UICapabilities>({
    ...readQueryPolicy,
    queryKey: ['ui-capabilities', host, userId, generation],
    enabled: userId != null,
    staleTime: 60_000,
    refetchInterval: (query) => readPollInterval(query, 60_000),
    refetchIntervalInBackground: false,
    queryFn: async ({ signal }) => {
      const { data } = await api.get('/api/aichat/ui/capabilities/', {
        baseURL: host,
        signal
      });
      if (data?.version !== 1 || typeof data.risk_radar !== 'boolean') {
        throw new InvalidReadResponse('Invalid UI capabilities');
      }
      return data as {
        version: 1;
        risk_radar: boolean;
        command_center: boolean;
        maintenance_metrics: number;
        backend_commit: string;
      };
    }
  });
}
