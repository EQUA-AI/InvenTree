/** Real login form and auth state, with network responses supplied by Playwright. */
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { api, setApiDefaults } from '../src/App';
import { AuthenticationForm } from '../src/components/forms/AuthenticationForm';
import { messages } from '../src/locales/en/messages';
import { useUserState } from '../src/states/UserState';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';

i18n.load('en', messages);
i18n.activate('en');
setApiDefaults();
// Exercise real Axios timeout handling without slowing the suite by 5s per case.
api.defaults.timeout = 500;

function Fixture() {
  const location = useLocation();
  return (
    <>
      <AuthenticationForm />
      <Button onClick={() => useUserState.getState().clearUserState()}>
        Clear anonymous state
      </Button>
      <div data-testid='destination'>{location.pathname}</div>
    </>
  );
}

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider>
      <Notifications autoClose={false} />
      <MemoryRouter initialEntries={['/login']}>
        <Fixture />
      </MemoryRouter>
    </MantineProvider>
  </I18nProvider>
);
