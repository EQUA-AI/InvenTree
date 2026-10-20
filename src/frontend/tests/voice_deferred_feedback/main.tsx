/**
 * Dedicated fixture for the voice-deferred-feedback Playwright suite
 * (VOICE-H01/H02/H05). Mounts the REAL owned voice surfaces with real state
 * stores on an isolated Vite server (port 5177): no business backend, no
 * auth, and no real microphone — getUserMedia is counted and always denied,
 * so a test can prove "invalid submit starts no microphone".
 */
import { i18n } from '@lingui/core';
import { MantineProvider } from '@mantine/core';
import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';

import type { VoiceClientState, VoiceError } from '../../lib/types/Voice';
import { VoiceSessionControl } from '../../src/components/ai/VoiceSessionControl';
import {
  VoiceActiveStrip,
  VoiceComposerControl
} from '../../src/components/ai/voice/VoiceComposerControl';
import { VoiceConsentDialog } from '../../src/components/ai/voice/VoiceConsentDialog';
import type { VoiceStartBlockReason } from '../../src/components/ai/voice/voiceStartEligibility';
import { useLocalState } from '../../src/states/LocalState';
import {
  useVoiceSessionState,
  useVoiceSurfaceState
} from '../../src/states/VoiceSessionState';

i18n.load('en', {});
i18n.activate('en');

const mic = { calls: 0 };
if (navigator.mediaDevices) {
  navigator.mediaDevices.getUserMedia = async () => {
    mic.calls += 1;
    throw new DOMException('microphone denied in fixture', 'NotAllowedError');
  };
}

function App() {
  const [state, setState] = useState<VoiceClientState>('ready');
  const [error, setError] = useState<VoiceError | null>(null);
  const [muted, setMuted] = useState(false);
  const startBlockedReason = useVoiceSurfaceState(
    (s) => s.startBlockedReason
  ) as VoiceStartBlockReason | null;
  const start = () => useVoiceSurfaceState.getState().requestStart();

  useEffect(() => {
    (window as unknown as Record<string, unknown>).__voiceFixture = {
      setClientState: setState,
      setError,
      micCount: () => mic.calls,
      surface: useVoiceSurfaceState,
      session: useVoiceSessionState,
      consentSnapshot: () => {
        const s = useLocalState.getState() as unknown as Record<
          string,
          unknown
        >;
        return {
          voiceLocale: s.voiceLocale,
          voiceConsentVersion: s.voiceConsentVersion
        };
      }
    };
  }, []);

  return (
    <MantineProvider>
      <VoiceSessionControl
        state={state}
        error={error}
        muted={muted}
        onStart={start}
        onEnd={() => {}}
        onCancel={() => {}}
        onToggleMute={() => setMuted((m) => !m)}
      />
      <VoiceComposerControl
        state={state}
        error={error}
        muted={muted}
        onStart={start}
        onEnd={() => {}}
        onCancel={() => {}}
        onToggleMute={() => {}}
        startBlockedReason={startBlockedReason}
      />
      <VoiceActiveStrip
        state={state}
        error={error}
        muted={muted}
        onStart={start}
        onEnd={() => {}}
        onCancel={() => {}}
        onToggleMute={() => {}}
        startBlockedReason={startBlockedReason}
      />
      <VoiceConsentDialog />
    </MantineProvider>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
