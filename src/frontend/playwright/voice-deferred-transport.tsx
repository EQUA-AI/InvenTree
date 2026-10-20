/**
 * VOICE-H03 + VOICE-H08 mounted transport fixture — MOCKED NETWORK + SYNTHETIC
 * MEDIA, REAL LIVE HOOK + REAL CONTROLLER.
 *
 * Mounts the production `useVoiceLiveSession` hook and the real consuming
 * surfaces (`VoiceSessionControl`, `VoiceTranscript`, `VoiceExperienceControls`,
 * `VoiceConsentDialog`) with the same wiring as the existing voice-session
 * fixture, so every request under test travels the real
 * `VoiceSessionController` path. All network endpoints are mocked and observed
 * by `tests/pages/voice_harness.ts` (`installVoiceMocks`) — no invented fetch
 * stubs, no live backend or provider, no real microphone (the harness installs
 * synthetic media devices). The /sdp metadata can be deferred by the spec via
 * `VoiceHarnessOptions.slowObservation` while the UI is visibly connecting.
 */
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { MantineProvider, Stack } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';

import { VoiceSessionControl } from '../src/components/ai/VoiceSessionControl';
import { VoiceTranscript } from '../src/components/ai/VoiceTranscript';
import { VoiceConsentDialog } from '../src/components/ai/voice/VoiceConsentDialog';
import { VoiceExperienceControls } from '../src/components/ai/voice/VoiceExperienceControls';
import { useVoiceLiveSession } from '../src/hooks/useVoiceLiveSession';
import { messages } from '../src/locales/en/messages';
import { useVoiceSessionState } from '../src/states/VoiceSessionState';
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
  const s = useVoiceSessionState();
  // The exact mounted confirm/discard handlers the buttons below call, exposed
  // so the spec can prove the controller gates them before connection and make
  // them idempotent afterwards. Never a bypass: the same `voice.confirmPending`
  // runs every gate (transport, fences, seen-dedupe).
  (window as any).__confirmPending = () => void voice.confirmPending();
  (window as any).__discardPending = voice.discardPending;
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
      {/* The visible warm-up/hold review strip (pendingConfirm render). */}
      <VoiceTranscript
        partial={s.partial}
        listening={s.mic === 'listening'}
        pendingConfirm={s.pendingConfirm}
        holdPrompt={s.holdPrompt}
      />
      {/* The visible notice render (warmup_held / warmup_discarded / ...). */}
      <VoiceExperienceControls />
    </Stack>
  );
}

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider>
      <QueryClientProvider client={client}>
        <VoiceConsentDialog />
        <Panel />
      </QueryClientProvider>
    </MantineProvider>
  </I18nProvider>
);
