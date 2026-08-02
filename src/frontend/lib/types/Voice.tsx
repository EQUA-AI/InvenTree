/**
 * Voice Live client types (WS5-T2).
 *
 * These mirror the authenticated backend payloads exactly. By design there
 * is no field for an Azure credential, provider URL, token, or session
 * configuration authority — the server never sends one and the client must
 * not model one.
 */

export type VoiceSessionState =
  | 'created'
  | 'connecting'
  | 'active'
  | 'ended'
  | 'failed'
  | 'expired';

/** User-visible client machine states (contract §10.1 / VA3). */
export type VoiceClientState =
  | 'unavailable'
  | 'ready'
  | 'connecting'
  | 'listening'
  | 'confirming'
  | 'reviewing'
  | 'speaking'
  | 'error';

export interface VoiceTransportsAllowed {
  webrtc: boolean;
  relay: boolean;
}

export interface VoiceSessionPayload {
  id: string;
  state: VoiceSessionState;
  thread_id: string;
  transport: 'webrtc' | 'relay' | null;
  transports_allowed: VoiceTransportsAllowed;
  webrtc_preview: boolean;
  turn_count: number;
  policy_version: string;
  terminal_reason: string | null;
}

export interface VoiceSpokenPayload {
  utterance_id: string;
  spoken_summary: string;
  spoken_summary_hash: string;
  playback_state: string;
}

export interface VoiceTurnResponse {
  session_id: string;
  thread_id: string;
  turn_id: string;
  message: string;
  workflow_used: string | null;
  response_state: string;
  replayed: boolean;
  spoken: VoiceSpokenPayload | null;
}

/** Stable backend error codes surfaced to the UI verbatim. */
export type VoiceErrorCode =
  | 'VOICE_SESSION_UNAVAILABLE'
  | 'VOICE_SESSION_LIMIT'
  | 'VOICE_SESSION_FORBIDDEN'
  | 'VOICE_SESSION_EXPIRED'
  | 'VOICE_SIGNALING_FAILED'
  | 'VOICE_TRANSPORT_UNAVAILABLE'
  | 'VOICE_TRANSCRIPT_INCOMPLETE'
  | 'VOICE_RESPONSE_INCOMPLETE'
  | 'IDEMPOTENCY_CONFLICT'
  | 'MICROPHONE_DENIED'
  | 'BROWSER_UNSUPPORTED';

export interface VoiceError {
  code: VoiceErrorCode;
  detail?: string;
}

export interface VoicePartialTranscript {
  text: string;
  itemId: string;
}

export interface VoiceFinalTranscript {
  text: string;
  itemId: string;
  confidence: number | null;
  language: string;
}
