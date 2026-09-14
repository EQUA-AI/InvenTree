/** Explicit-route fixture; all network paths are recording-only browser mocks. */
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, MantineProvider, Stack, Text } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Link,
  MemoryRouter,
  Route,
  Routes,
  useLocation
} from 'react-router-dom';
import { PhoneVoiceRouting } from '../src/components/ai/voice/PhoneVoiceRouting';
import { messages } from '../src/locales/en/messages';
import { useLocalState } from '../src/states/LocalState';
import { useUserState } from '../src/states/UserState';
import VoiceMobileAppView from '../src/views/VoiceMobileAppView';
import '@mantine/core/styles.css';

i18n.load('en', messages);
i18n.activate('en');
useLocalState.setState({
  getHost: () => window.location.origin,
  allowMobile: !window.location.search.includes('pilot')
});
if (!window.location.search.includes('signed_out')) {
  useUserState.setState({
    is_authed: true,
    user: { pk: 11, username: 'VOICE-TEST' } as any
  });
}
function LoggedOut() {
  const allowMobile = useLocalState((s) => s.allowMobile);
  useEffect(
    () => useUserState.setState({ is_authed: false, user: undefined }),
    []
  );
  return <Text>Signed out. Full app preference: {String(allowMobile)}</Text>;
}
function SignIn() {
  const location = useLocation();
  return (
    <Text>Sign in required. Return to: {location.state?.redirectUrl}</Text>
  );
}
const client = new QueryClient({
  defaultOptions: { queries: { retry: false } }
});
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider i18n={i18n}>
      <MantineProvider>
        <QueryClientProvider client={client}>
          <MemoryRouter
            initialEntries={[
              window.location.search.includes('pilot') ? '/' : '/voice'
            ]}
          >
            <PhoneVoiceRouting>
              <Routes>
                <Route path='/voice' element={<VoiceMobileAppView />} />
                <Route
                  path='/'
                  element={
                    <Stack>
                      <Text>Full app fixture</Text>
                      <Button component={Link} to='/voice'>
                        Return to voice fixture
                      </Button>
                    </Stack>
                  }
                />
                <Route path='/logout' element={<LoggedOut />} />
                <Route path='/logged-in' element={<SignIn />} />
              </Routes>
            </PhoneVoiceRouting>
          </MemoryRouter>
        </QueryClientProvider>
      </MantineProvider>
    </I18nProvider>
  </StrictMode>
);
