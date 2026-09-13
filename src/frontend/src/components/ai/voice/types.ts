import type {
  VoiceClientState,
  VoiceError,
  VoiceFinalTranscript,
  VoiceHoldPrompt,
  VoicePartialTranscript,
  VoiceSessionPayload,
  VoiceSpokenPayload,
  VoiceTurnResponse
} from '../../../../lib/types/Voice';

export type ListeningMode = 'continuous' | 'push_to_talk';
export type VoiceNotice =
  | 'hidden'
  | 'ended_away'
  | 'idle_ended'
  | 'reconnecting'
  | 'reconnected'
  | 'connection_failed'
  | 'queue_full'
  | 'audio_blocked'
  | 'tts_timeout'
  | 'mic_silent'
  | 'route_changed'
  | 'sounds_off'
  | 'readback_available'
  | null;
export interface VoiceCapability {
  enabled: boolean;
  foreground_session: boolean;
  decisions: boolean;
  prompts: boolean;
  help: boolean;
  presentation: boolean;
  mobile_surface: boolean;
  wake_lock: boolean;
  ice_servers: RTCIceServer[];
  modes: ListeningMode[];
  default_mode: ListeningMode;
  max_queued_turns: number;
  idle_timeout_s: number;
  mic_silence_rms: number;
  mic_silence_window_s: number;
  route_loss_pause: boolean;
  tts_timeout_s: number;
  decision_max_armed_s: number;
  reconnect: { grace_s: number; max_attempts: number };
  confidence_floor: number;
  locales: string[];
  voices: { locale: string; voice: string }[];
  default_locale: string;
  default_voice: string;
  consent_version: string;
  voice_write_locales: string[];
}
export interface VoicePreferences {
  voiceListeningMode: ListeningMode;
  voiceEarcons: boolean;
  voiceSrAnnounceTranscripts: boolean;
  voiceConsentVersion: string;
  voiceLocale: string;
  voiceOutputVoice: string;
  voiceSpeakerNoticeSeen: boolean;
}
export interface VoiceSnapshot {
  state: VoiceClientState;
  mic: 'off' | 'muted' | 'listening' | 'ptt_idle' | 'suspended';
  playback: 'idle' | 'pending' | 'playing' | 'paused' | 'blocked' | 'text_only';
  transport: 'off' | 'connecting' | 'connected' | 'reconnecting' | 'failed';
  persistence: 'keep_on_minimize' | 'end_on_close';
  mode: ListeningMode;
  session: VoiceSessionPayload | null;
  capability: VoiceCapability | null;
  error: VoiceError | null;
  muted: boolean;
  hidden: boolean;
  notice: VoiceNotice;
  partial: VoicePartialTranscript | null;
  pendingConfirm: VoiceFinalTranscript | null;
  revisions: VoiceFinalTranscript[];
  holdPrompt: VoiceHoldPrompt | null;
  lastSpoken: VoiceSpokenPayload | null;
  presentation: VoiceTurnResponse['presentation'];
  lastSubmitted: {
    itemId: string;
    threadId: string;
    sessionId: string;
    status: 'pending' | 'complete' | 'unconfirmed';
  } | null;
  queued: number;
  outputs: { value: string; label: string }[];
  outputDevice: string;
  outputSelectionSupported: boolean;
}
export const initialVoiceSnapshot: VoiceSnapshot = {
  state: 'unavailable',
  mic: 'off',
  playback: 'idle',
  transport: 'off',
  persistence: 'end_on_close',
  mode: 'continuous',
  session: null,
  capability: null,
  error: null,
  muted: false,
  hidden: false,
  notice: null,
  partial: null,
  pendingConfirm: null,
  revisions: [],
  holdPrompt: null,
  lastSpoken: null,
  presentation: null,
  lastSubmitted: null,
  queued: 0,
  outputs: [],
  outputDevice: '',
  outputSelectionSupported: false
};
export function legacyClientState(s: VoiceSnapshot): VoiceClientState {
  if (!s.capability?.enabled) return 'unavailable';
  if (s.error && !s.session) return 'error';
  if (s.transport === 'connecting' || s.transport === 'reconnecting')
    return 'connecting';
  if (!s.session) return 'ready';
  if (s.pendingConfirm) return 'confirming';
  if (s.lastSubmitted?.status === 'pending') return 'reviewing';
  if (s.playback === 'playing' || s.playback === 'pending') return 'speaking';
  return 'listening';
}
