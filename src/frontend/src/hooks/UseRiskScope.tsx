import { useLocalStorage } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../contexts/ApiContext';
import { useUserState } from '../states/UserState';

export interface RiskScopeState {
  scopes: string[];
  scope: string;
  authorizationFingerprint: string;
  setScope: (scope: string) => void;
  unavailable: boolean;
  isLoading: boolean;
}

interface RiskScopeResponse {
  scopes: string[];
  authorization_fingerprint: string;
}

/**
 * Shared scope-selection hook for Risk Radar / Command Center surfaces.
 *
 * Fetches the viewer's authorized scope keys from the server, and persists
 * the chosen scope key in local storage. The server list is authoritative:
 * a stored scope which is no longer authorized is reset to the first
 * available scope and is never sent as the active scope.
 */
export function useRiskScope(): RiskScopeState {
  const api = useApi();
  const userId = useUserState((state) => state.user?.pk);

  // Synchronous read: with the default deferred hydration, a remount with
  // a warm query cache would run the reset effect against the pre-hydration
  // default value and clobber the user's stored selection.
  const [storedScope, setStoredScope] = useLocalStorage<string>({
    key: 'risk-radar-scope',
    defaultValue: '',
    getInitialValueInEffect: false
  });

  const scopesQuery = useQuery({
    queryKey: ['risk-scopes', userId],
    enabled: userId != null,
    retry: false,
    gcTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: 'always',
    queryFn: () =>
      api.get(apiUrl(ApiEndpoints.risk_scope_list)).then((response) => {
        const data = response.data as Partial<RiskScopeResponse>;
        if (
          !Array.isArray(data?.scopes) ||
          !data.scopes.every((scope) => typeof scope === 'string') ||
          typeof data.authorization_fingerprint !== 'string' ||
          !data.authorization_fingerprint
        ) {
          throw new Error('Invalid risk scope response');
        }
        return data as RiskScopeResponse;
      })
  });

  // Never expose cached authorization data while the server is revalidating
  // the current user. This prevents a previous session's scope and findings
  // from rendering during a background refetch.
  const scopeDataReady = scopesQuery.isSuccess && !scopesQuery.isFetching;
  const scopes: string[] = useMemo(
    () => (scopeDataReady ? (scopesQuery.data?.scopes ?? []) : []),
    [scopeDataReady, scopesQuery.data]
  );
  const authorizationFingerprint = scopeDataReady
    ? (scopesQuery.data?.authorization_fingerprint ?? '')
    : '';

  // The feature is unavailable if the endpoint errors (e.g. HTTP 404 when
  // the feature flag is off) or the viewer has no authorized scopes.
  const unavailable: boolean =
    scopesQuery.isError || (scopesQuery.isSuccess && scopes.length === 0);

  // Reset an unauthorized stored scope to the first available scope. An
  // empty stored value is left alone: the displayed scope falls back to
  // scopes[0] below without writing storage, so a user who has never
  // chosen a scope is not silently pinned to one.
  useEffect(() => {
    if (!scopesQuery.isSuccess || scopes.length === 0) {
      return;
    }
    if (storedScope && !scopes.includes(storedScope)) {
      setStoredScope(scopes[0]);
    }
  }, [scopesQuery.isSuccess, scopes, storedScope, setStoredScope]);

  // Never expose a stored scope which the server has not authorized
  const scope: string = useMemo(() => {
    if (storedScope && scopes.includes(storedScope)) {
      return storedScope;
    }
    return scopes[0] ?? '';
  }, [storedScope, scopes]);

  return {
    scopes,
    scope,
    authorizationFingerprint,
    setScope: setStoredScope,
    unavailable,
    isLoading: scopesQuery.isLoading || scopesQuery.isFetching
  };
}
