import type { AxiosResponse } from 'axios';
import { create } from 'zustand';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import type { ModelType } from '@lib/enums/ModelType';
import { UserPermissions, type UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import type { UserProps, UserStateProps } from '@lib/types/User';
import { api, queryClient, setApiDefaults } from '../App';
import { useAIChatState } from './AIChatState';
import { useLocalState } from './LocalState';
import { useServerApiState } from './ServerApiState';

let tokenRequest = 0;
let profileRequest = 0;

/**
 * Global user information state, using Zustand manager
 */
export const useUserState = create<UserStateProps>((set, get) => ({
  user: undefined,
  is_authed: false,
  authStatus: 'unknown',
  authGeneration: 0,
  setAuthenticated: (authed = true) => {
    if (!authed) {
      get().clearUserState();
      return;
    }
    set({
      is_authed: authed,
      authStatus: authed ? 'authenticated' : 'unauthenticated',
      authGeneration: get().authGeneration + 1
    });
    setApiDefaults();
  },
  userId: () => {
    const user: UserProps = get().user as UserProps;
    return user?.pk;
  },
  username: () => {
    const user: UserProps = get().user as UserProps;

    if (user?.first_name || user?.last_name) {
      return `${user.first_name} ${user.last_name}`.trim();
    } else {
      return user?.username ?? '';
    }
  },
  setUser: (newUser: UserProps | undefined) => {
    const previous = get().user;
    // Profile/theme updates do not reset chat. Identity and entitlement
    // changes clear the entire chat subtree, including pending async work.
    const boundary = (user?: UserProps) =>
      JSON.stringify([
        user?.pk,
        user?.roles,
        user?.permissions,
        user?.groups,
        user?.is_staff,
        user?.is_superuser
      ]);
    if (previous && boundary(previous) !== boundary(newUser)) {
      useAIChatState.getState().resetSession();
      queryClient.clear();
    }
    set({ user: newUser });
  },
  getUser: () => get().user,
  clearUserState: () => {
    useAIChatState.getState().resetSession();
    queryClient.clear();
    set({
      user: undefined,
      is_authed: false,
      authStatus: 'unauthenticated',
      authGeneration: get().authGeneration + 1
    });
    // Anonymous session checks must not erase the cookie needed by login.
    // Explicit logout clears it after ending the server session.
    setApiDefaults();
  },
  fetchUserToken: async () => {
    const host = useLocalState.getState().getHost();
    const generation = get().authGeneration;
    const request = ++tokenRequest;
    const current = () =>
      request === tokenRequest &&
      generation === get().authGeneration &&
      host === useLocalState.getState().getHost();
    set({ authStatus: 'checking' });
    // Session cookies are HttpOnly; document.cookie cannot establish expiry.
    try {
      const response = await api.get(apiUrl(ApiEndpoints.auth_session), {
        baseURL: host
      });
      if (!current()) return 'stale';
      if (
        response.status === 200 &&
        response.data?.meta?.is_authenticated === true
      ) {
        const id = response.data?.data?.user?.id;
        if (id && get().user && id !== get().user?.pk) get().setUser(undefined);
        set({ is_authed: true, authStatus: 'authenticated' });
        return 'authenticated';
      }
      if (
        response.status === 200 &&
        response.data?.meta?.is_authenticated === false
      ) {
        get().clearUserState();
        return 'unauthenticated';
      }
    } catch (err: any) {
      if (!current()) return 'stale';
      if (err?.response?.status === 401) {
        useServerApiState.getState().setAuthContext(err.response.data?.data);
        get().clearUserState();
        return 'unauthenticated';
      }
    }
    set({ authStatus: 'unavailable' });
    return 'unavailable';
  },
  fetchUserState: async (checkSession = false) => {
    const host = useLocalState.getState().getHost();
    const generation = get().authGeneration;
    const request = ++profileRequest;
    const current = () =>
      request === profileRequest &&
      generation === get().authGeneration &&
      host === useLocalState.getState().getHost();
    if (checkSession || !get().isAuthed()) {
      const result = await get().fetchUserToken();
      if (result !== 'authenticated') return result;
    }
    if (!current()) return 'stale';
    set({ authStatus: 'checking' });

    // Fetch user data along with role/permission data in a single request -
    // the '?roles=true' param asks the API to include the same role and
    // permission data that used to require a separate request to
    // user_me_roles.
    let response: AxiosResponse;
    try {
      response = await api.get(apiUrl(ApiEndpoints.user_me), {
        baseURL: host,
        params: { roles: true }
      });
    } catch (err: any) {
      if (!current()) return 'stale';
      // A forbidden profile alone does not prove that the session expired.
      if ([401, 403].includes(err?.response?.status)) {
        const result = await get().fetchUserToken();
        if (result !== 'authenticated') return result;
        if (!current()) return 'stale';
      }
      set({ authStatus: 'unavailable' });
      return 'unavailable';
    }
    if (!current()) return 'stale';
    if (
      response.status !== 200 ||
      !Number.isSafeInteger(response.data?.pk) ||
      response.data.pk <= 0
    ) {
      set({ authStatus: 'unavailable' });
      return 'unavailable';
    }

    const user: UserProps = {
      pk: response.data.pk,
      first_name: response.data?.first_name ?? '',
      last_name: response.data?.last_name ?? '',
      email: response.data.email,
      username: response.data.username,
      groups: response.data.groups,
      profile: response.data.profile,
      roles: response.data?.roles ?? {},
      permissions: response.data?.permissions ?? {},
      is_staff: response.data?.is_staff ?? false,
      is_superuser: response.data?.is_superuser ?? false
    };
    get().setUser(user);
    set({ authStatus: 'authenticated' });
    return 'authenticated';
  },
  isAuthed: () => {
    return get().is_authed;
  },
  isLoggedIn: () => {
    if (!get().isAuthed()) {
      return false;
    }
    const user: UserProps = get().user as UserProps;
    return !!user && !!user.pk;
  },
  isStaff: () => {
    const user: UserProps = get().user as UserProps;
    return user?.is_staff ?? false;
  },
  isSuperuser: () => {
    const user: UserProps = get().user as UserProps;
    return user?.is_superuser ?? false;
  },
  checkUserRole: (role: UserRoles, permission: UserPermissions) => {
    // Check if the user has the specified permission for the specified role
    const user: UserProps = get().user as UserProps;

    if (!user) {
      return false;
    }

    if (user?.is_superuser) return true;
    if (user?.roles === undefined) return false;
    if (user?.roles[role] === undefined) return false;
    if (user?.roles[role] === null) return false;

    return user?.roles[role]?.includes(permission) ?? false;
  },
  hasDeleteRole: (role: UserRoles) => {
    return get().checkUserRole(role, UserPermissions.delete);
  },
  hasChangeRole: (role: UserRoles) => {
    return get().checkUserRole(role, UserPermissions.change);
  },
  hasAddRole: (role: UserRoles) => {
    return get().checkUserRole(role, UserPermissions.add);
  },
  hasViewRole: (role: UserRoles) => {
    return get().checkUserRole(role, UserPermissions.view);
  },
  checkUserPermission: (model: ModelType, permission: UserPermissions) => {
    // Check if the user has the specified permission for the specified model
    const user: UserProps = get().user as UserProps;

    if (!user) {
      return false;
    }

    if (user?.is_superuser) return true;

    if (user?.permissions === undefined) return false;
    if (user?.permissions[model] === undefined) return false;
    if (user?.permissions[model] === null) return false;

    return user?.permissions[model]?.includes(permission) ?? false;
  },
  hasDeletePermission: (model: ModelType) => {
    return get().checkUserPermission(model, UserPermissions.delete);
  },
  hasChangePermission: (model: ModelType) => {
    return get().checkUserPermission(model, UserPermissions.change);
  },
  hasAddPermission: (model: ModelType) => {
    return get().checkUserPermission(model, UserPermissions.add);
  },
  hasViewPermission: (model: ModelType) => {
    return get().checkUserPermission(model, UserPermissions.view);
  },
  // login state
  login_checked: false,
  setLoginChecked: (value) => {
    set({ login_checked: value });
  }
}));
