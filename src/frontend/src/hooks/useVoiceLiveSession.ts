import { useQuery } from '@tanstack/react-query';
/** Thin React adapter: mounting/unmounting a surface never owns foreground media. */
import { useEffect, useRef } from 'react';
import type {
  VoiceFinalTranscript,
  VoiceTurnResponse
} from '../../lib/types/Voice';
import type { VoiceCapability } from '../components/ai/voice/types';
import { voiceHttp } from '../components/ai/voice/voiceHttp';
import {
  useVoiceSessionState,
  useVoiceSurfaceState,
  voiceController
} from '../states/VoiceSessionState';

export interface UseVoiceLiveSessionOptions {
  host: string;
  enabled: boolean;
  threadId?: string;
  onTurnResult?: (turn: VoiceTurnResponse) => void;
  onFinalTranscript?: (value: VoiceFinalTranscript) => void;
}
export function useVoiceLiveSession(options: UseVoiceLiveSessionOptions) {
  const snapshot = useVoiceSessionState();
  const callbacks = useRef(options);
  callbacks.current = options;
  const capability = useQuery({
    queryKey: ['voice-capability', options.host],
    enabled: options.enabled,
    queryFn: () => voiceHttp<VoiceCapability>(options.host, 'capability'),
    staleTime: 60_000,
    refetchInterval: (query) =>
      query.state.data?.runtime?.available === false ? 5_000 : 60_000,
    refetchIntervalInBackground: false
  });
  useEffect(() => {
    voiceController.configure(options.host, options.threadId);
    voiceController.setCapability(
      options.enabled ? (capability.data ?? null) : null
    );
  }, [options.host, options.threadId, options.enabled, capability.data]);
  useEffect(() => {
    const listener = {
      onTurnResult: (turn: VoiceTurnResponse) =>
        callbacks.current.onTurnResult?.(turn),
      onFinalTranscript: (value: VoiceFinalTranscript) =>
        callbacks.current.onFinalTranscript?.(value)
    };
    voiceController.listeners.add(listener);
    // StrictMode can replay this subscription, but cannot start/end the session.
    return () => {
      voiceController.listeners.delete(listener);
    };
  }, []);
  return {
    ...snapshot,
    start: useVoiceSurfaceState.getState().requestStart,
    end: voiceController.end,
    cancel: voiceController.cancel,
    toggleMute: voiceController.toggleMute,
    submitTranscript: voiceController.submitTranscript,
    confirmPending: voiceController.confirmPending,
    discardPending: voiceController.discardPending,
    minimize: voiceController.minimize
  };
}
export type UseVoiceLiveSessionResult = ReturnType<typeof useVoiceLiveSession>;
