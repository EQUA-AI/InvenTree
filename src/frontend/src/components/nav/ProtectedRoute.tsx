import type { JSX } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useUserState } from '../../states/UserState';
import { SessionRecovery } from '../forms/SessionRecovery';

/** Shared authentication boundary for application routes. */
export function ProtectedRoute({ children }: { children: JSX.Element }) {
  const location = useLocation();
  const { isLoggedIn, authStatus } = useUserState();
  if (authStatus === 'unavailable' || authStatus === 'checking')
    return <SessionRecovery />;
  if (!isLoggedIn())
    return (
      <Navigate
        to='/logged-in'
        state={{
          redirectUrl: location.pathname,
          queryParams: location.search,
          anchor: location.hash
        }}
      />
    );
  return children;
}
