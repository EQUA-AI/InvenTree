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

// S43: the session/turn payload shapes are GENERATED from the backend's
// pydantic wire models (ai/core/voice/wire.py) — re-exported here so every
// existing import keeps working while drift is structurally impossible.
export type {
  VoicePendingQuestion,
  VoicePendingQuestionOption,
  VoiceSessionPayload,
  VoiceSpokenPayload,
  VoiceTransportsAllowed,
  VoiceTurnResponse
} from './AimmsWire.generated';
import type { ServerVoiceErrorCode } from './AimmsWire.generated';

/** Codes only the CLIENT mints (never sent by the server). */
export type ClientVoiceErrorCode = 'MICROPHONE_DENIED' | 'BROWSER_UNSUPPORTED';

/** Stable error codes surfaced to the UI verbatim (server ∪ client). */
export type VoiceErrorCode = ServerVoiceErrorCode | ClientVoiceErrorCode;

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
