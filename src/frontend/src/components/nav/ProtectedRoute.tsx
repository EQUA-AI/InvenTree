import type { JSX } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useUserState } from '../../states/UserState';

/** Shared authentication boundary for application routes. */
export function ProtectedRoute({ children }: { children: JSX.Element }) {
  const location = useLocation();
  const { isLoggedIn } = useUserState();
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
