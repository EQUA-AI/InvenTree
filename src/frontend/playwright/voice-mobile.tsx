/** Unified assistant fixture; endpoints are recording-only browser mocks. */
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import {
  Button,
  Group,
  MantineProvider,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { QueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Link,
  MemoryRouter,
  Outlet,
  Route,
  Routes,
  useLocation
} from 'react-router-dom';
import { VoiceConsentDialog } from '../src/components/ai/voice/VoiceConsentDialog';
import { VoiceGlobalIndicator } from '../src/components/ai/voice/VoiceGlobalIndicator';
import { VoiceHandsFreeContent } from '../src/components/ai/voice/VoiceHandsFreeSurface';
import { AssistantSurface } from '../src/components/aichat/AssistantSurface';
import { EvidenceChips } from '../src/components/aichat/EvidenceChips';
import { GlobalEvidenceViewer } from '../src/components/aichat/MediaEvidenceModal';
import { useAssistantNavigation } from '../src/components/aichat/useAssistantNavigation';
import { ProtectedRoute } from '../src/components/nav/ProtectedRoute';
import { ApiProvider } from '../src/contexts/ApiContext';
import { useVoiceLiveSession } from '../src/hooks/useVoiceLiveSession';
import { messages } from '../src/locales/en/messages';
import { useAIChatState } from '../src/states/AIChatState';
import { useEvidenceViewerState } from '../src/states/EvidenceViewerState';
import { useLocalState } from '../src/states/LocalState';
import { useUserState } from '../src/states/UserState';
import {
  useVoiceSurfaceState,
  voiceController
} from '../src/states/VoiceSessionState';
import '@mantine/core/styles.css';

i18n.load('en', messages);
i18n.activate('en');
useLocalState.setState({ getHost: () => window.location.origin });
useAIChatState.getState().open();
if (!window.location.search.includes('signed_out')) {
  useUserState.setState({
    is_authed: true,
    user: { pk: 11, username: 'VOICE-TEST' } as any
  });
}
function SignIn() {
  const location = useLocation();
  return (
    <Text>Sign in required. Return to: {location.state?.redirectUrl}</Text>
  );
}
function Fixture() {
  useEffect(() => {
    const nextCitation = () => {
      const { item, open } = useEvidenceViewerState.getState();
      if (item) open({ ...item, page_index: 1, label: 'Evidence page 2' });
    };
    window.addEventListener('fixture-evidence-next', nextCitation);
    return () =>
      window.removeEventListener('fixture-evidence-next', nextCitation);
  }, []);
  const onNavigate = useAssistantNavigation();
  const voice = useVoiceLiveSession({
    host: `${window.location.origin}/api/ai`,
    enabled: true,
    threadId: 'thread_mocked'
  });
  const narrow = useMediaQuery('(max-width: 48em)');
  const opened = useAIChatState((s) => s.isOpen);
  const consent = useVoiceSurfaceState((s) => s.consent);
  const evidenceOpen = useEvidenceViewerState((s) => s.item !== null);
  const [draft, setDraft] = useState('');
  const close = () => {
    void voiceController.end();
    useVoiceSurfaceState.getState().closeConsent();
    useAIChatState.getState().close();
  };
  useEffect(
    () => () => {
      void voiceController.end();
    },
    []
  );
  return (
    <>
      <Stack p='md' data-testid='background-app'>
        <Outlet />
        <Button
          aria-controls='ai-chat-drawer'
          onClick={() => useAIChatState.getState().open()}
        >
          Open AI Assistant
        </Button>
        <Button component={Link} to='/other'>
          Navigate background
        </Button>
      </Stack>
      <VoiceGlobalIndicator />
      <VoiceConsentDialog />
      <GlobalEvidenceViewer />
      <AssistantSurface
        opened={opened}
        modal={Boolean(narrow)}
        suspended={consent || evidenceOpen}
        width={550}
        title='AI Assistant'
        onClose={close}
      >
        <Stack p='md' style={{ overflowY: 'auto' }}>
          <Group justify='space-between'>
            <Text>Scope: Authorized records</Text>
            <Button onClick={close}>Close assistant</Button>
          </Group>
          {voice.capability?.enabled ? (
            <VoiceHandsFreeContent embedded />
          ) : (
            <Text>Voice is unavailable. You can continue typing.</Text>
          )}
          <TextInput
            label='Message draft'
            value={draft}
            onChange={(event) => setDraft(event.currentTarget.value)}
          />
          <EvidenceChips
            items={[0, 1].map((page_index) => ({
              attachment_id: 9,
              model_type: 'part',
              model_id: 1,
              media_type: 'document',
              segment_index: 0,
              page_index,
              source_revision: 'a'.repeat(64),
              label: `Evidence page ${page_index + 1}`
            }))}
          />
          <Button component={Link} to='/other' onClick={onNavigate}>
            Open related record
          </Button>
          <Button
            onClick={() => {
              close();
              useUserState.getState().clearUserState();
            }}
          >
            Log out
          </Button>
        </Stack>
      </AssistantSurface>
    </>
  );
}
const client = new QueryClient({
  defaultOptions: { queries: { retry: false } }
});
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider i18n={i18n}>
      <MantineProvider>
        <ApiProvider api={axios.create()} client={client}>
          <MemoryRouter
            initialEntries={[
              window.location.search.includes('old_route') ? '/voice' : '/'
            ]}
          >
            <Routes>
              <Route
                element={
                  <ProtectedRoute>
                    <Fixture />
                  </ProtectedRoute>
                }
              >
                <Route index element={<Text>Full app fixture</Text>} />
                <Route
                  path='/other'
                  element={<Text>Related record fixture</Text>}
                />
              </Route>
              <Route path='/logged-in' element={<SignIn />} />
              <Route path='*' element={<Text>Page not found</Text>} />
            </Routes>
          </MemoryRouter>
        </ApiProvider>
      </MantineProvider>
    </I18nProvider>
  </StrictMode>
);
