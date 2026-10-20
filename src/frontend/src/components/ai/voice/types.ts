import type { RuntimeAvailability } from '../../../../lib/types/AimmsWire.generated';
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
import type { VoiceNetworkDiagnostics } from './networkDiagnostics';

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
  | 'warmup_held'
  | 'warmup_discarded'
  | null;
export interface VoiceCapability {
  enabled: boolean;
  runtime?: RuntimeAvailability;
  foreground_session: boolean;
  validation_metrics?: boolean;
  decisions: boolean;
  inventory_actions?: boolean;
  procedure_complete?: boolean;
  guided_procedures?: boolean;
  closeout?: boolean;
  turn?: false;
  turn_provider?: null;
  direct_connect_timeout_s?: number;
  network_qualification?: string;
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
  /**
   * Spoken progress preference for NEW requests (a mid-flight server request
   * keeps the preference it was admitted with). `off` suppresses only the
   * optional spoken waiting cues — errors, clarifications, decision readbacks,
   * useful answers and help always speak. Earcon and screen-reader transcript
   * preferences stay independent. The controller sends `progress_mode` only
   * when the preference is set (server default `brief`).
   */
  voiceProgressMode?: VoiceProgressMode;
}
export type VoiceProgressMode = 'off' | 'brief' | 'guided';
export const DEFAULT_VOICE_PROGRESS_MODE: VoiceProgressMode = 'brief';
/** Persisted/entered values are normalized safely to the Brief default. */
export function normalizeVoiceProgressMode(value: unknown): VoiceProgressMode {
  return value === 'off' || value === 'guided' ? value : 'brief';
}
/** zustand-persist merge: keep stored state, renormalize the progress mode. */
export function mergeVoicePreferences<S extends object>(
  persisted: unknown,
  current: S
): S & { voiceProgressMode?: VoiceProgressMode } {
  const source =
    persisted && typeof persisted === 'object'
      ? (persisted as Record<string, unknown>)
      : {};
  const merged = {
    ...current,
    ...source
  } as S & { voiceProgressMode?: VoiceProgressMode };
  merged.voiceProgressMode = normalizeVoiceProgressMode(
    source.voiceProgressMode
  );
  return merged;
}
export interface VoiceSnapshot {
  networkDiagnostics: VoiceNetworkDiagnostics | null;
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
  networkDiagnostics: null,
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
  if (!s.capability?.enabled || s.capability.runtime?.available === false)
    return 'unavailable';
  if (s.error && !s.session) return 'error';
  if (s.transport === 'connecting' || s.transport === 'reconnecting')
    return 'connecting';
  if (!s.session) return 'ready';
  if (s.pendingConfirm) return 'confirming';
  if (s.lastSubmitted?.status === 'pending') return 'reviewing';
  if (s.playback === 'playing' || s.playback === 'pending') return 'speaking';
  return 'listening';
}
