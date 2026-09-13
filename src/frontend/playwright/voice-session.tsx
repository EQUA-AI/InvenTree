/** Local-only component fixture: every endpoint is mocked by its browser test. */
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, MantineProvider, Stack, TextInput } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { VoiceSessionControl } from '../src/components/ai/VoiceSessionControl';
import { VoiceConsentDialog } from '../src/components/ai/voice/VoiceConsentDialog';
import { VoiceExperienceControls } from '../src/components/ai/voice/VoiceExperienceControls';
import { VoiceGlobalIndicator } from '../src/components/ai/voice/VoiceGlobalIndicator';
import { VoiceHandsFreeSurface } from '../src/components/ai/voice/VoiceHandsFreeSurface';
import { useVoiceLiveSession } from '../src/hooks/useVoiceLiveSession';
import { messages } from '../src/locales/en/messages';
import { voiceController } from '../src/states/VoiceSessionState';
import '@mantine/core/styles.css';

i18n.load('en', messages);
i18n.activate('en');
const client = new QueryClient({
  defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
});
function Panel() {
  const voice = useVoiceLiveSession({
    host: `${window.location.origin}/api/ai`,
    enabled: true,
    threadId: 'thread_mocked'
  });
  return (
    <Stack data-voice-surface>
      <VoiceSessionControl
        {...voice}
        onStart={voice.start}
        onEnd={() => void voice.end()}
        onCancel={() => void voice.cancel()}
        onToggleMute={voice.toggleMute}
        onConfirmTranscript={() => void voice.confirmPending()}
        onDiscardTranscript={voice.discardPending}
      />
      <VoiceExperienceControls />
    </Stack>
  );
}
function Fixture() {
  const [panel, setPanel] = useState(true);
  return (
    <Stack p='md'>
      <VoiceGlobalIndicator />
      <VoiceConsentDialog />
      <VoiceHandsFreeSurface />
      <Button
        onClick={() => {
          window.history.pushState(
            {},
            '',
            `?page=${panel ? 'elsewhere' : 'chat'}`
          );
          setPanel(!panel);
        }}
      >
        Navigate and toggle panel
      </Button>
      <Button onClick={() => voiceController.logout()}>Test logout</Button>
      <TextInput label='Barcode input' data-barcode-input />
      {panel && <Panel />}
    </Stack>
  );
}
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider i18n={i18n}>
      <MantineProvider>
        <QueryClientProvider client={client}>
          <Fixture />
        </QueryClientProvider>
      </MantineProvider>
    </I18nProvider>
  </StrictMode>
);
