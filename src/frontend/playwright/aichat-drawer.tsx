/**
 * AI chat drawer browser fixture (plan packages A/B/E).
 *
 * The REAL drawer, decision card, proposal list, consent dialog, hands-free
 * surface and global voice indicator under a real MemoryRouter and the real
 * React Query cache; auth is wholly mocked (seeded UserState) and every API
 * response is supplied by Playwright route mocks in the specs. This is
 * MOCKED BROWSER RENDERING — not a real backend E2E run. No credentials,
 * no backend, no database. The provider follows the emulated system theme.
 */
import './aichat-drawer-lingui';

import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, MantineProvider, Stack } from '@mantine/core';
import '@mantine/core/styles.css';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';

import { api, queryClient } from '../src/App';
import { VoiceConsentDialog } from '../src/components/ai/voice/VoiceConsentDialog';
import { VoiceGlobalIndicator } from '../src/components/ai/voice/VoiceGlobalIndicator';
import { VoiceHandsFreeSurface } from '../src/components/ai/voice/VoiceHandsFreeSurface';
import { AIChatButton, AIChatDrawer } from '../src/components/nav/AIChatDrawer';
import { ApiProvider } from '../src/contexts/ApiContext';
import { openGlobalAIChat, useAIChatState } from '../src/states/AIChatState';
import { useUserState } from '../src/states/UserState';
import { useVoiceSurfaceState } from '../src/states/VoiceSessionState';

// Wholly mocked auth: no login request, no credentials.
useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: { pk: 11, username: 'Drawer fixture', is_superuser: true } as any
});

function DrawerFixture() {
  const opened = useAIChatState((state) => state.isOpen);
  return (
    <Stack p='md' gap='xs'>
      <Button
        data-testid='fixture-ask-machine'
        onClick={() =>
          openGlobalAIChat({ machineId: 7, machineName: 'Pump A' })
        }
      >
        Ask about Pump A
      </Button>
      <AIChatButton
        opened={opened}
        onClick={() => {
          const state = useAIChatState.getState();
          if (opened) state.close();
          else state.open();
        }}
      />
      <AIChatDrawer
        opened={opened}
        onClose={() => useAIChatState.getState().close()}
      />
      <VoiceGlobalIndicator />
      <VoiceConsentDialog />
      <VoiceHandsFreeSurface />
    </Stack>
  );
}

// Test hooks for flows with no visible control in the fixture.
(window as any).__aiDrawerTest = {
  ask: () => openGlobalAIChat({ machineId: 7, machineName: 'Pump A' }),
  openHandsFree: () => useVoiceSurfaceState.getState().openFullscreen(),
  closeHandsFree: () => useVoiceSurfaceState.getState().closeFullscreen()
};

const initialRoute =
  new URLSearchParams(window.location.search).get('route') ??
  '/machines/index/';

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider defaultColorScheme='auto'>
      <ApiProvider api={api} client={queryClient}>
        <MemoryRouter initialEntries={[initialRoute]}>
          <DrawerFixture />
        </MemoryRouter>
      </ApiProvider>
    </MantineProvider>
  </I18nProvider>
);
