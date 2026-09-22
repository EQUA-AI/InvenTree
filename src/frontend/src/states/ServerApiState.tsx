import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { AuthConfig, AuthContext } from '@lib/types/Auth';
import { api } from '../App';
import { emptyServerAPI } from '../defaults/defaults';
import { useLocalState } from './LocalState';
import type { ServerAPIProps } from './states';

interface ServerApiStateProps {
  server: ServerAPIProps;
  setServer: (newServer: ServerAPIProps) => void;
  fetchServerApiState: (force?: boolean) => Promise<void>;
  auth_config?: AuthConfig;
  auth_context?: AuthContext;
  setAuthContext: (auth_context: AuthContext | undefined) => void;
  mfa_context?: any;
  setMfaContext: (mfa_context: any) => void;
  // Helper functions
  sso_enabled: () => boolean;
  registration_enabled: () => boolean;
  sso_registration_enabled: () => boolean;
  password_forgotten_enabled: () => boolean;
}

function get_server_setting(val: any) {
  if (val === null || val === undefined) {
    return false;
  }
  return val;
}

let pendingServerApiFetch: {
  host: string;
  task: Promise<void>;
  controller: AbortController;
} | null = null;
let fetchedServerHost: string | null = null;
let activeServerHost: string | null = null;

export const useServerApiState = create<ServerApiStateProps>()(
  persist(
    (set, get) => ({
      server: emptyServerAPI,
      setServer: (newServer: ServerAPIProps) => set({ server: newServer }),
      fetchServerApiState: async (force = false) => {
        const host = useLocalState.getState().getHost();
        if (!force && pendingServerApiFetch?.host === host)
          return pendingServerApiFetch.task;
        if (!force && fetchedServerHost === host && activeServerHost === host)
          return;

        pendingServerApiFetch?.controller.abort();
        if (activeServerHost !== host) {
          set({
            server: emptyServerAPI,
            auth_config: undefined,
            auth_context: undefined,
            mfa_context: undefined
          });
          activeServerHost = host;
          fetchedServerHost = null;
        }
        const controller = new AbortController();
        const current = () =>
          pendingServerApiFetch?.controller === controller &&
          useLocalState.getState().getHost() === host;
        const task = Promise.all([
          api.get(apiUrl(ApiEndpoints.api_server_info), {
            baseURL: host,
            signal: controller.signal
          }),
          api.get(apiUrl(ApiEndpoints.auth_config), {
            baseURL: host,
            signal: controller.signal,
            headers: { Authorization: '' }
          })
        ])
          .then(([server, auth]) => {
            if (!current()) return;
            if (
              !server.data ||
              typeof server.data !== 'object' ||
              !auth.data?.data ||
              typeof auth.data.data !== 'object'
            ) {
              throw new Error('Invalid server metadata');
            }
            set({ server: server.data, auth_config: auth.data.data });
            fetchedServerHost = host;
          })
          .catch(() => {
            // A failed request must remain retryable, including a failed force refresh.
            if (current()) {
              fetchedServerHost = null;
              console.error('ERR: Error fetching server metadata');
            }
          })
          .finally(() => {
            if (pendingServerApiFetch?.controller === controller)
              pendingServerApiFetch = null;
          });
        pendingServerApiFetch = { host, task, controller };
        await task;
      },
      auth_config: undefined,
      auth_context: undefined,
      setAuthContext(auth_context) {
        set({ auth_context });
      },
      mfa_context: undefined,
      setMfaContext(mfa_context) {
        set({ mfa_context });
      },
      sso_enabled: () => {
        if (!get_server_setting(get().server?.settings?.sso_enabled)) {
          return false;
        }
        const data = get().auth_config?.socialaccount.providers;
        return !(data === undefined || data.length == 0);
      },
      registration_enabled: () => {
        return get_server_setting(get().server?.settings?.registration_enabled);
      },
      sso_registration_enabled: () => {
        return get_server_setting(get().server?.settings?.sso_registration);
      },
      password_forgotten_enabled: () => {
        return get_server_setting(
          get().server?.settings?.password_forgotten_enabled
        );
      }
    }),
    {
      name: 'server-api-state',
      storage: createJSONStorage(() => sessionStorage)
    }
  )
);
