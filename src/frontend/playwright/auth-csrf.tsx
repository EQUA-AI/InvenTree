/** Real login form and auth state, with network responses supplied by Playwright. */
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { createRoot } from 'react-dom/client';
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate
} from 'react-router-dom';
import { api, setApiDefaults } from '../src/App';
import { AuthenticationForm } from '../src/components/forms/AuthenticationForm';
import { ProtectedRoute } from '../src/components/nav/ProtectedRoute';
import { doLogout } from '../src/functions/auth';
import { messages } from '../src/locales/en/messages';
import LoggedIn from '../src/pages/Auth/LoggedIn';
import Login from '../src/pages/Auth/Login';
import { useLocalState } from '../src/states/LocalState';
import { useUserState } from '../src/states/UserState';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';

i18n.load('en', messages);
i18n.activate('en');
setApiDefaults();
// Exercise real Axios timeout handling without slowing the suite by 5s per case.
api.defaults.timeout =
  Number(new URLSearchParams(window.location.search).get('timeout')) || 500;

function Fixture() {
  const location = useLocation();
  const navigate = useNavigate();
  const state = useUserState();
  return (
    <>
      <Routes>
        <Route path='/logged-in' element={<LoggedIn />} />
        <Route
          path='/home'
          element={
            <ProtectedRoute>
              <div>Private account page</div>
            </ProtectedRoute>
          }
        />
        <Route
          path='*'
          element={
            new URLSearchParams(window.location.search).has('auto') ? (
              <Login />
            ) : (
              <AuthenticationForm />
            )
          }
        />
      </Routes>
      <Button onClick={() => useUserState.getState().clearUserState()}>
        Clear anonymous state
      </Button>
      <Button onClick={() => state.fetchUserState(true)}>
        Recheck session
      </Button>
      <Button onClick={() => doLogout(navigate)}>Explicit sign out</Button>
      <Button
        onClick={() => {
          useLocalState.getState().setHost('http://localhost:5173', 'other');
          navigate('/login');
        }}
      >
        Change server
      </Button>
      <div data-testid='session-state'>
        {JSON.stringify({
          status: state.authStatus,
          user: state.user?.pk,
          authenticated: state.is_authed
        })}
      </div>
      <div data-testid='destination'>{location.pathname}</div>
    </>
  );
}

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider>
      <Notifications autoClose={false} />
      <MemoryRouter
        initialEntries={[
          new URLSearchParams(window.location.search).get('restore')
            ? '/logged-in'
            : new URLSearchParams(window.location.search).has('auto')
              ? '/login?login=fixture-user&password=fixture-password'
              : '/login'
        ]}
      >
        <Fixture />
      </MemoryRouter>
    </MantineProvider>
  </I18nProvider>
);
