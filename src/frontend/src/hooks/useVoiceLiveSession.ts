/**
 * useVoiceLiveSession (WS5-T3): explicit, bounded, visible realtime session.
 *
 * The hook owns microphone permission, the preview-flagged WebRTC transport,
 * the `voice-live-events` data channel, truthful state, and cleanup. It never
 * interprets a transcript as an action: a completed transcript is submitted
 * to the authenticated `/voice/sessions/{id}/turns` endpoint, and everything
 * an answer may do happens server-side. No Azure credential ever reaches
 * this module; the browser exchanges only session-bound SDP with AIMMS.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type {
  VoiceClientState,
  VoiceError,
  VoiceErrorCode,
  VoiceFinalTranscript,
  VoicePartialTranscript,
  VoiceSessionPayload,
  VoiceTurnResponse
} from '../../lib/types/Voice';
import {
  DEFAULT_CONFIDENCE_FLOOR,
  needsConfirmation
} from '../components/ai/voiceCriticalTerms';

const DATA_CHANNEL_LABEL = 'voice-live-events';
const FINAL_EVENT = 'conversation.item.input_audio_transcription.completed';
const PARTIAL_EVENT = 'conversation.item.input_audio_transcription.delta';

function getCsrfCookie(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

function jsonHeaders(unsafe: boolean): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json'
  };
  if (unsafe) {
    const token = getCsrfCookie();
    if (token) {
      headers['X-CSRFToken'] = token;
    }
  }
  return headers;
}

function resolveUrl(path: string, host: string): string {
  return new URL(path, `${host.replace(/\/$/, '')}/`).toString();
}

async function readErrorCode(response: Response): Promise<VoiceErrorCode> {
  try {
    const body = await response.json();
    if (typeof body?.detail === 'string') {
      return body.detail as VoiceErrorCode;
    }
  } catch {
    // fall through to the generic code below
  }
  return 'VOICE_SESSION_UNAVAILABLE';
}

export interface UseVoiceLiveSessionOptions {
  /** AI backend base, e.g. `${apiHost}/api/ai`. */
  host: string;
  /** Server capability: render nothing when false. */
  enabled: boolean;
  /** Optional existing thread to attach to. */
  threadId?: string;
  /** Called once per completed application turn. */
  onTurnResult?: (turn: VoiceTurnResponse) => void;
  /** Called for each completed user transcript before submission. */
  onFinalTranscript?: (transcript: VoiceFinalTranscript) => void;
}

export interface UseVoiceLiveSessionResult {
  state: VoiceClientState;
  session: VoiceSessionPayload | null;
  partial: VoicePartialTranscript | null;
  error: VoiceError | null;
  muted: boolean;
  /** Explicit user start — the consent act (owner decision 2026-07-15). */
  start: () => Promise<void>;
  end: () => Promise<void>;
  cancel: () => Promise<void>;
  toggleMute: () => void;
  /** Submit a (possibly user-corrected) completed transcript as a turn. */
  submitTranscript: (transcript: VoiceFinalTranscript) => Promise<void>;
  /** Transcript held for critical-term / low-confidence confirmation. */
  pendingTranscript: VoiceFinalTranscript | null;
  confirmPendingTranscript: (correctedText?: string) => Promise<void>;
  discardPendingTranscript: () => void;
  /** Server-configured ASR confidence floor for critical-term review. */
  confidenceFloor: number;
}

export function useVoiceLiveSession(
  options: UseVoiceLiveSessionOptions
): UseVoiceLiveSessionResult {
  const { host, enabled, threadId, onTurnResult, onFinalTranscript } = options;

  const [state, setState] = useState<VoiceClientState>(
    enabled ? 'ready' : 'unavailable'
  );
  const [session, setSession] = useState<VoiceSessionPayload | null>(null);
  const [partial, setPartial] = useState<VoicePartialTranscript | null>(null);
  const [error, setError] = useState<VoiceError | null>(null);
  const [muted, setMuted] = useState<boolean>(false);
  const [pendingTranscript, _setPendingTranscript] =
    useState<VoiceFinalTranscript | null>(null);
  const pendingTranscriptRef = useRef<VoiceFinalTranscript | null>(null);
  const setPendingTranscript = useCallback(
    (value: VoiceFinalTranscript | null) => {
      pendingTranscriptRef.current = value;
      _setPendingTranscript(value);
    },
    []
  );

  const peerRef = useRef<RTCPeerConnection | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const sessionRef = useRef<VoiceSessionPayload | null>(null);
  const submittedItemsRef = useRef<Set<string>>(new Set());

  // Server capability probe: the control renders only when the deployment
  // flag is on AND this actor is in the pilot cohort. The probe discloses
  // nothing beyond booleans and never errors visibly.
  const [serverEnabled, setServerEnabled] = useState<boolean>(false);
  const [confidenceFloor, setConfidenceFloor] = useState<number>(
    DEFAULT_CONFIDENCE_FLOOR
  );
  useEffect(() => {
    let cancelled = false;
    if (!enabled) {
      setServerEnabled(false);
      return;
    }
    (async () => {
      try {
        const response = await fetch(resolveUrl('voice/capability', host), {
          method: 'GET',
          headers: jsonHeaders(false),
          credentials: 'include'
        });
        if (!cancelled && response.ok) {
          const body = (await response.json()) as {
            enabled?: boolean;
            confidence_floor?: number;
          };
          setServerEnabled(Boolean(body.enabled));
          if (typeof body.confidence_floor === 'number') {
            setConfidenceFloor(body.confidence_floor);
          }
          return;
        }
      } catch {
        // fall through to disabled
      }
      if (!cancelled) {
        setServerEnabled(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled, host]);

  const effectiveEnabled = enabled && serverEnabled;

  useEffect(() => {
    setState((current) =>
      effectiveEnabled
        ? current === 'unavailable'
          ? 'ready'
          : current
        : 'unavailable'
    );
  }, [effectiveEnabled]);

  const releaseMedia = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    peerRef.current?.close();
    peerRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.srcObject = null;
      audioRef.current = null;
    }
  }, []);

  const fail = useCallback(
    (code: VoiceErrorCode, detail?: string) => {
      releaseMedia();
      setError({ code, detail });
      setState('error');
    },
    [releaseMedia]
  );

  const submitTranscript = useCallback(
    async (transcript: VoiceFinalTranscript) => {
      const active = sessionRef.current;
      if (!active) {
        return;
      }
      if (submittedItemsRef.current.has(transcript.itemId)) {
        return;
      }
      submittedItemsRef.current.add(transcript.itemId);
      setState('reviewing');
      try {
        const response = await fetch(
          resolveUrl(`voice/sessions/${active.id}/turns`, host),
          {
            method: 'POST',
            headers: jsonHeaders(true),
            credentials: 'include',
            body: JSON.stringify({
              transcript: transcript.text,
              item_id: transcript.itemId,
              confidence: transcript.confidence,
              language: transcript.language
            })
          }
        );
        if (!response.ok) {
          fail(await readErrorCode(response));
          return;
        }
        const turn = (await response.json()) as VoiceTurnResponse;
        onTurnResult?.(turn);
        setPartial(null);
        setState(turn.spoken ? 'speaking' : 'listening');
        if (!turn.spoken) {
          return;
        }
        // Playback arrives on the WebRTC audio track; return to listening
        // when the element goes quiet or shortly after, whichever is first.
        window.setTimeout(() => {
          setState((current) =>
            current === 'speaking' ? 'listening' : current
          );
        }, 15_000);
      } catch {
        fail('VOICE_RESPONSE_INCOMPLETE');
      }
    },
    [fail, host, onTurnResult]
  );

  const handleDataChannelMessage = useCallback(
    (raw: string) => {
      let event: Record<string, unknown>;
      try {
        event = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        return;
      }
      const type = String(event.type ?? '');
      if (type === PARTIAL_EVENT) {
        setPartial({
          text: String(event.delta ?? event.transcript ?? ''),
          itemId: String(event.item_id ?? '')
        });
        return;
      }
      if (type === FINAL_EVENT) {
        const finalTranscript: VoiceFinalTranscript = {
          text: String(event.transcript ?? ''),
          itemId: String(event.item_id ?? ''),
          confidence:
            typeof event.confidence === 'number' ? event.confidence : null,
          language: String(event.language ?? 'en-US')
        };
        if (!finalTranscript.text || !finalTranscript.itemId) {
          return;
        }
        onFinalTranscript?.(finalTranscript);
        // Critical terms and low-confidence transcripts require a visible
        // typed/tap confirmation before they become an application turn.
        if (
          needsConfirmation(
            finalTranscript.text,
            finalTranscript.confidence,
            confidenceFloor
          )
        ) {
          setPartial(null);
          setPendingTranscript(finalTranscript);
          return;
        }
        void submitTranscript(finalTranscript);
      }
    },
    [onFinalTranscript, submitTranscript]
  );

  const confirmPendingTranscript = useCallback(
    async (correctedText?: string) => {
      const pendingFinal = pendingTranscriptRef.current;
      if (!pendingFinal) {
        return;
      }
      const text = (correctedText ?? pendingFinal.text).trim();
      setPendingTranscript(null);
      if (!text) {
        return;
      }
      await submitTranscript({ ...pendingFinal, text });
    },
    [submitTranscript]
  );

  const discardPendingTranscript = useCallback(() => {
    setPendingTranscript(null);
  }, []);

  const start = useCallback(async () => {
    if (!effectiveEnabled || sessionRef.current) {
      return;
    }
    setError(null);
    setState('connecting');

    if (
      typeof RTCPeerConnection === 'undefined' ||
      !navigator.mediaDevices?.getUserMedia
    ) {
      fail('BROWSER_UNSUPPORTED');
      return;
    }

    let created: VoiceSessionPayload;
    try {
      const response = await fetch(resolveUrl('voice/sessions', host), {
        method: 'POST',
        headers: jsonHeaders(true),
        credentials: 'include',
        body: JSON.stringify({ thread_id: threadId ?? null })
      });
      if (!response.ok) {
        fail(await readErrorCode(response));
        return;
      }
      created = (await response.json()) as VoiceSessionPayload;
    } catch {
      fail('VOICE_SESSION_UNAVAILABLE');
      return;
    }
    sessionRef.current = created;
    setSession(created);

    if (!created.transports_allowed.webrtc) {
      // No qualified audio transport: report honestly; typed chat remains.
      await endInternal('transport_unavailable');
      fail('VOICE_TRANSPORT_UNAVAILABLE');
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      await endInternal('microphone_denied');
      fail('MICROPHONE_DENIED');
      return;
    }
    streamRef.current = stream;

    try {
      const peer = new RTCPeerConnection();
      peerRef.current = peer;
      stream.getAudioTracks().forEach((track) => peer.addTrack(track, stream));
      peer.addEventListener('track', (event) => {
        const audio = new Audio();
        audio.autoplay = true;
        audio.srcObject = event.streams[0] ?? new MediaStream([event.track]);
        audioRef.current = audio;
      });
      const channel = peer.createDataChannel(DATA_CHANNEL_LABEL);
      channel.addEventListener('message', (event) =>
        handleDataChannelMessage(String(event.data))
      );
      peer.addEventListener('connectionstatechange', () => {
        if (
          peer.connectionState === 'failed' ||
          peer.connectionState === 'disconnected'
        ) {
          fail('VOICE_SIGNALING_FAILED', 'peer connection lost');
        }
      });

      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      const response = await fetch(
        resolveUrl(`voice/sessions/${created.id}/sdp`, host),
        {
          method: 'POST',
          headers: jsonHeaders(true),
          credentials: 'include',
          body: JSON.stringify({ sdp_offer: offer.sdp ?? '' })
        }
      );
      if (!response.ok) {
        const code = await readErrorCode(response);
        await endInternal('signaling_failed');
        fail(code);
        return;
      }
      const body = (await response.json()) as { sdp_answer: string };
      await peer.setRemoteDescription({ type: 'answer', sdp: body.sdp_answer });
      setState('listening');
    } catch {
      await endInternal('signaling_failed');
      fail('VOICE_SIGNALING_FAILED');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveEnabled, fail, handleDataChannelMessage, host, threadId]);

  const endInternal = useCallback(
    async (_reason: string) => {
      const active = sessionRef.current;
      sessionRef.current = null;
      submittedItemsRef.current.clear();
      releaseMedia();
      setPartial(null);
      setPendingTranscript(null);
      if (!active) {
        return;
      }
      try {
        await fetch(resolveUrl(`voice/sessions/${active.id}`, host), {
          method: 'DELETE',
          headers: jsonHeaders(true),
          credentials: 'include'
        });
      } catch {
        // The server sweeper reconciles sessions we could not end cleanly.
      }
      setSession(null);
    },
    [host, releaseMedia]
  );

  const end = useCallback(async () => {
    await endInternal('user_ended');
    setState(effectiveEnabled ? 'ready' : 'unavailable');
  }, [effectiveEnabled, endInternal]);

  const cancel = useCallback(async () => {
    const active = sessionRef.current;
    if (audioRef.current) {
      audioRef.current.pause();
    }
    if (!active) {
      return;
    }
    try {
      await fetch(resolveUrl(`voice/sessions/${active.id}/cancel`, host), {
        method: 'POST',
        headers: jsonHeaders(true),
        credentials: 'include'
      });
    } catch {
      // Cancellation is best-effort; ending the session always cleans up.
    }
    setState('listening');
  }, [host]);

  const toggleMute = useCallback(() => {
    const stream = streamRef.current;
    if (!stream) {
      return;
    }
    const next = !muted;
    stream.getAudioTracks().forEach((track) => {
      track.enabled = !next;
    });
    setMuted(next);
  }, [muted]);

  // Release the microphone on unmount/navigation — no background listening.
  useEffect(() => {
    return () => {
      void endInternal('unmounted');
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    state,
    session,
    partial,
    error,
    muted,
    start,
    end,
    cancel,
    toggleMute,
    submitTranscript,
    pendingTranscript,
    confirmPendingTranscript,
    discardPendingTranscript,
    confidenceFloor
  };
}
