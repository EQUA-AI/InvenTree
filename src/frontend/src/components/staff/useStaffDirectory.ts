import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type {
  StaffMember,
  StaffPage,
  StaffScope,
  StaffTrade
} from '@lib/types/Staff';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useApi } from '../../contexts/ApiContext';
import { useLocalState } from '../../states/LocalState';
import { useUserState } from '../../states/UserState';
import { staffCapabilities, staffScopeParams } from './staffAssignment';

export function useStaffAccess() {
  const user = useUserState((state) => state.user);
  const generation = useUserState((state) => state.authGeneration);
  const host = useLocalState((state) => state.getHost());
  return {
    ...staffCapabilities(user),
    // Include the authority boundary even before the global cache is cleared.
    cacheKey: [
      host,
      user?.pk,
      generation,
      JSON.stringify(user?.permissions),
      !!user?.is_superuser
    ]
  };
}

export function useStaffMember(
  scope: StaffScope,
  userId: number | null,
  enabled = true
) {
  const api = useApi();
  const access = useStaffAccess();
  return useQuery({
    queryKey: ['staff-member', ...access.cacheKey, scope, userId],
    enabled: access.directory && enabled && userId != null,
    retry: false,
    staleTime: 0,
    gcTime: 0,
    queryFn: async ({ signal }) => {
      const { data } = await api.get<StaffPage<StaffMember>>(
        apiUrl(ApiEndpoints.staff_directory),
        {
          signal,
          params: { ...staffScopeParams(scope), user_id: userId, limit: 1 }
        }
      );
      return data.results.find((person) => person.user_id === userId) ?? null;
    }
  });
}

export function useStaffCandidates(
  scope: StaffScope,
  search: string,
  enabled: boolean
) {
  const api = useApi();
  const access = useStaffAccess();
  return useInfiniteQuery({
    queryKey: ['staff-directory', ...access.cacheKey, scope, search],
    enabled: access.directory && enabled,
    retry: false,
    staleTime: 0,
    gcTime: 0,
    initialPageParam: 0,
    queryFn: async ({ pageParam, signal }) => {
      const { data } = await api.get<StaffPage<StaffMember>>(
        apiUrl(ApiEndpoints.staff_directory),
        {
          signal,
          params: {
            ...staffScopeParams(scope),
            search,
            limit: 25,
            offset: pageParam
          }
        }
      );
      return data;
    },
    getNextPageParam: (last, pages) => {
      const count = pages.reduce((sum, page) => sum + page.results.length, 0);
      return last.results.length > 0 && count < last.count ? count : undefined;
    }
  });
}

export function useStaffTrades(enabled = true) {
  const api = useApi();
  const access = useStaffAccess();
  return useInfiniteQuery({
    queryKey: ['staff-trades', ...access.cacheKey],
    enabled,
    retry: false,
    gcTime: 0,
    initialPageParam: 0,
    queryFn: async ({ pageParam, signal }) => {
      const { data } = await api.get<StaffPage<StaffTrade>>(
        apiUrl(ApiEndpoints.user_trades),
        {
          signal,
          params: { limit: 100, offset: pageParam }
        }
      );
      return data;
    },
    getNextPageParam: (last, pages) => {
      const count = pages.reduce((sum, page) => sum + page.results.length, 0);
      return last.results.length > 0 && count < last.count ? count : undefined;
    }
  });
}
