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
  detectCriticalSpans
} from '../components/ai/voiceCriticalTerms';

const DATA_CHANNEL_LABEL = 'voice-live-events';
const FINAL_EVENT = 'conversation.item.input_audio_transcription.completed';
const PARTIAL_EVENT = 'conversation.item.input_audio_transcription.delta';

// Pure-filler utterances that should never become application turns.
const FILLER_ONLY = /^(?:uh|um|hmm+|mm+|mhm|huh|erm|ah|oh)[.!?,\s]*$/i;

// Hands-free review decisions: ONLY an utterance that is entirely one of
// these phrases acts on a held transcript. Anything longer is ignored — a
// sentence containing "yes" or "cancel" must not decide a safety review.
const VOICE_CONFIRM_RE =
  /^(?:confirm|yes|yes please|continue|send it|submit|go ahead|that's right|correct)$/;
const VOICE_DISCARD_RE =
  /^(?:discard|cancel|no|nope|scratch that|start over|delete|discard it|try again)$/;

// A public STUN server lets the browser discover its server-reflexive
// candidate so the peer can be reached across NAT. It carries no credentials,
// so nothing provider-authoritative ever reaches this module. Fully locked-
// down networks (symmetric NAT / UDP-blocked) may additionally need a TURN
// relay, which is a separate deliberate deployment decision.
const DEFAULT_ICE_SERVERS: RTCIceServer[] = [
  { urls: 'stun:stun.l.google.com:19302' }
];
// The SDP is relayed once with no trickle-ICE channel, so the offer must
// already carry the browser's candidates. Cap gathering so a stalled
// candidate never blocks the call forever.
const ICE_GATHERING_TIMEOUT_MS = 2500;
// Backstop: if the media/data path never connects, fail honestly instead of
// showing a 'listening' UI that captures nothing.
const MEDIA_CONNECT_TIMEOUT_MS = 12_000;

/** Resolve once ICE candidate gathering completes or the cap elapses. */
function waitForIceGathering(
  peer: RTCPeerConnection,
  timeoutMs: number
): Promise<void> {
  if (peer.iceGatheringState === 'complete') {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) {
        return;
      }
      settled = true;
      peer.removeEventListener('icegatheringstatechange', onChange);
      window.clearTimeout(timer);
      resolve();
    };
    const onChange = () => {
      if (peer.iceGatheringState === 'complete') {
        finish();
      }
    };
    peer.addEventListener('icegatheringstatechange', onChange);
    const timer = window.setTimeout(finish, timeoutMs);
  });
}

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
  /**
   * Submit a completed transcript as a turn. The voice loop is hands-free:
   * completed transcripts are auto-submitted and the spoken answer is the
   * correction loop. Confirmation of critical values remains a requirement
   * of structured use (fault/closeout capture), not of advisory chat.
   */
  submitTranscript: (transcript: VoiceFinalTranscript) => Promise<void>;
  /** Transcript held for confirmation (critical terms or low confidence). */
  pendingConfirm: VoiceFinalTranscript | null;
  /** Submit the held transcript exactly as heard. */
  confirmPending: () => Promise<void>;
  /** Discard the held transcript and resume listening. */
  discardPending: () => void;
}

export function useVoiceLiveSession(
  options: UseVoiceLiveSessionOptions
): UseVoiceLiveSessionResult {
  const { host, enabled, threadId, onTurnResult, onFinalTranscript } = options;

  const [pendingConfirm, setPendingConfirm] =
    useState<VoiceFinalTranscript | null>(null);
  // Ref mirror for event handlers; state alone is stale inside the data
  // channel callback. Set both through setHold only.
  const pendingConfirmRef = useRef<VoiceFinalTranscript | null>(null);
  const confidenceFloorRef = useRef<number>(DEFAULT_CONFIDENCE_FLOOR);
  const [state, setState] = useState<VoiceClientState>(
    enabled ? 'ready' : 'unavailable'
  );
  const [session, setSession] = useState<VoiceSessionPayload | null>(null);
  const [partial, setPartial] = useState<VoicePartialTranscript | null>(null);
  const [error, setError] = useState<VoiceError | null>(null);
  const [muted, setMuted] = useState<boolean>(false);

  const peerRef = useRef<RTCPeerConnection | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const sessionRef = useRef<VoiceSessionPayload | null>(null);
  const submittedItemsRef = useRef<Set<string>>(new Set());
  const connectTimerRef = useRef<number | null>(null);
  const speakingTimerRef = useRef<number | null>(null);
  const speakingSinceRef = useRef<number>(0);
  const submitQueueRef = useRef<VoiceFinalTranscript[]>([]);
  const submittingRef = useRef<boolean>(false);

  const clearSpeakingTimer = useCallback(() => {
    if (speakingTimerRef.current !== null) {
      window.clearTimeout(speakingTimerRef.current);
      speakingTimerRef.current = null;
    }
  }, []);

  // Server capability probe: the control renders for authenticated users when
  // the deployment flag is on. The probe discloses nothing beyond booleans and
  // never errors visibly.
  const [serverEnabled, setServerEnabled] = useState<boolean>(false);
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
          if (typeof body.confidence_floor === 'number') {
            confidenceFloorRef.current = body.confidence_floor;
          }
          setServerEnabled(Boolean(body.enabled));
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
    if (connectTimerRef.current !== null) {
      window.clearTimeout(connectTimerRef.current);
      connectTimerRef.current = null;
    }
    clearSpeakingTimer();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    peerRef.current?.close();
    peerRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.srcObject = null;
      audioRef.current = null;
    }
  }, [clearSpeakingTimer]);

  const fail = useCallback(
    (code: VoiceErrorCode, detail?: string) => {
      releaseMedia();
      // Clear the session handle so a subsequent start() is not blocked by the
      // `sessionRef.current` guard. Several failure paths reach fail() without
      // going through endInternal(), which would otherwise leave the control
      // permanently unable to reconnect. The server session expires on its own.
      sessionRef.current = null;
      setSession(null);
      pendingConfirmRef.current = null;
      setPendingConfirm(null);
      setError({ code, detail });
      setState('error');
    },
    [releaseMedia]
  );

  const setHold = useCallback((held: VoiceFinalTranscript | null) => {
    pendingConfirmRef.current = held;
    setPendingConfirm(held);
  }, []);

  const submitNow = useCallback(
    async (transcript: VoiceFinalTranscript) => {
      const active = sessionRef.current;
      if (!active) {
        return;
      }
      // An earlier turn completing must never knock the UI out of the
      // confirmation hold; the held transcript owns the state until the
      // technician resolves it.
      setState((current) => (current === 'confirming' ? current : 'reviewing'));
      // Status speech (the thinking/failure phrases) streams on the shared
      // element while the turn is still processing, so resume it up front
      // if an earlier cancel() paused it. Autoplay-policy rejections retry
      // on the next user gesture.
      const audio = audioRef.current;
      if (audio?.paused) {
        audio.play().catch(() => {
          const resume = () => {
            audioRef.current?.play().catch(() => {});
          };
          window.addEventListener('pointerdown', resume, { once: true });
        });
      }
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
          const code = await readErrorCode(response);
          if (
            code === 'VOICE_RESPONSE_INCOMPLETE' ||
            code === 'VOICE_TRANSCRIPT_INCOMPLETE' ||
            code === 'IDEMPOTENCY_CONFLICT'
          ) {
            // Turn-level failure: keep the session and microphone alive so
            // the spoken failure phrase can play and the technician can
            // simply try again by voice.
            setError({ code });
            setPartial(null);
            setState((current) =>
              current === 'confirming' ? current : 'listening'
            );
            return;
          }
          fail(code);
          return;
        }
        const turn = (await response.json()) as VoiceTurnResponse;
        onTurnResult?.(turn);
        setPartial(null);
        // Only a dispatched TTS request ('requested') produces audio; a
        // persisted-but-undelivered utterance ('pending') stays text-only.
        const playbackRequested = turn.spoken?.playback_state === 'requested';
        if (playbackRequested) {
          speakingSinceRef.current = Date.now();
        }
        setState((current) =>
          current === 'confirming'
            ? current
            : playbackRequested
              ? 'speaking'
              : 'listening'
        );
        if (!playbackRequested) {
          return;
        }
        // The data channel's response.audio.done / barge-in events drive the
        // return to listening; this timer is only a fallback for a lost
        // event so the loop can never wedge in 'speaking'.
        clearSpeakingTimer();
        speakingTimerRef.current = window.setTimeout(() => {
          speakingTimerRef.current = null;
          setState((current) =>
            current === 'speaking' ? 'listening' : current
          );
        }, 15_000);
      } catch {
        fail('VOICE_RESPONSE_INCOMPLETE');
      }
    },
    [clearSpeakingTimer, fail, host, onTurnResult]
  );

  // Hands-free utterances can complete while the previous turn is still
  // processing; a serial queue keeps turns (and their spoken answers)
  // ordered instead of racing the state machine and the provider TTS slot.
  const drainQueue = useCallback(async () => {
    if (submittingRef.current) {
      return;
    }
    submittingRef.current = true;
    try {
      while (submitQueueRef.current.length > 0) {
        const next = submitQueueRef.current.shift();
        if (next) {
          await submitNow(next);
        }
      }
    } finally {
      submittingRef.current = false;
    }
  }, [submitNow]);

  const submitTranscript = useCallback(
    async (transcript: VoiceFinalTranscript) => {
      if (submittedItemsRef.current.has(transcript.itemId)) {
        return;
      }
      submittedItemsRef.current.add(transcript.itemId);
      if (submitQueueRef.current.length >= 3) {
        // Keep the conversation current rather than replaying a backlog.
        submitQueueRef.current.shift();
      }
      submitQueueRef.current.push(transcript);
      void drainQueue();
    },
    [drainQueue]
  );

  /** Submit the held transcript exactly as heard. */
  const confirmPending = useCallback(async () => {
    const held = pendingConfirmRef.current;
    if (!held) {
      return;
    }
    setHold(null);
    // Leave 'confirming' first so submitNow's hold-aware transitions run
    // normally; the dedupe/queue semantics of submitTranscript are intact
    // because the hold happened BEFORE the submitted-items set saw this
    // itemId.
    setState('reviewing');
    await submitTranscript(held);
  }, [setHold, submitTranscript]);

  /** Drop the held transcript; the technician re-speaks instead. */
  const discardPending = useCallback(() => {
    setHold(null);
    setState((current) => (current === 'confirming' ? 'listening' : current));
  }, [setHold]);

  const handleDataChannelMessage = useCallback(
    (raw: string) => {
      let event: Record<string, unknown>;
      try {
        event = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        return;
      }
      const type = String(event.type ?? '');
      if (type === 'error') {
        // Provider-side failure (for example a rejected speech request).
        // Transcripts and typed chat keep working; keep it diagnosable.
        console.warn('Voice Live provider error event', event.error ?? event);
        return;
      }
      if (type === 'input_audio_buffer.speech_started') {
        // Barge-in: the technician talking always wins over playback. The
        // provider stops synthesis server-side (interrupt_response) and the
        // live track simply goes quiet — never pause the local element here,
        // or later status/answer speech plays into a dead element.
        clearSpeakingTimer();
        setState((current) => (current === 'speaking' ? 'listening' : current));
        return;
      }
      if (type === 'response.audio.done' || type === 'response.done') {
        // Playback for this response has drained; resume the loop precisely
        // instead of waiting out the fallback timer. Ignore terminal events
        // that raced ahead of a just-started answer (the cancelled thinking
        // phrase drains milliseconds before the answer begins).
        if (Date.now() - speakingSinceRef.current < 750) {
          return;
        }
        clearSpeakingTimer();
        setState((current) => (current === 'speaking' ? 'listening' : current));
        return;
      }
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
        // Ambient-noise guard: skip transcripts with no letters or digits
        // and pure filler utterances so a VAD blip never becomes a turn.
        const trimmed = finalTranscript.text.trim();
        if (!/[\p{L}\p{N}]/u.test(trimmed) || FILLER_ONLY.test(trimmed)) {
          return;
        }
        // The hold stays exclusive: while a transcript awaits confirmation,
        // speech never replaces it or submits around it. But a hands-free
        // technician must be able to DECIDE by voice (battery feedback,
        // 2026-08-08): an utterance that is exactly a confirm phrase submits
        // the held transcript, exactly a discard phrase drops it, and
        // anything else is still ignored so the safety utterance under
        // review cannot be overwritten by more speech.
        if (pendingConfirmRef.current) {
          const decision = trimmed
            .toLowerCase()
            .replace(/[.!?,]+$/g, '')
            .trim();
          if (VOICE_CONFIRM_RE.test(decision)) {
            void confirmPending();
          } else if (VOICE_DISCARD_RE.test(decision)) {
            discardPending();
          }
          return;
        }
        onFinalTranscript?.(finalTranscript);
        // Critical-terms hold (WS5-T7, execution plan S7): a transcript that
        // carries safety-relevant content — measurements, negations, LOTO
        // terms, identifiers — or arrived measurably below the server's ASR
        // confidence floor waits for explicit confirmation instead of
        // auto-submitting: "15 psi" vs "50 psi", or a dropped "not", changes
        // a repair. A missing confidence field is NOT treated as low
        // confidence — providers may omit it, and holding every utterance
        // would kill the hands-free loop.
        const critical = detectCriticalSpans(finalTranscript.text).length > 0;
        const lowConfidence =
          typeof finalTranscript.confidence === 'number' &&
          finalTranscript.confidence < confidenceFloorRef.current;
        if (critical || lowConfidence) {
          setHold(finalTranscript);
          setState('confirming');
          return;
        }
        void submitTranscript(finalTranscript);
      }
    },
    [
      clearSpeakingTimer,
      onFinalTranscript,
      submitTranscript,
      setHold,
      confirmPending,
      discardPending
    ]
  );

  const start = useCallback(async () => {
    if (!effectiveEnabled || sessionRef.current) {
      return;
    }
    setError(null);
    pendingConfirmRef.current = null;
    setPendingConfirm(null);
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
      const peer = new RTCPeerConnection({ iceServers: DEFAULT_ICE_SERVERS });
      peerRef.current = peer;
      stream.getAudioTracks().forEach((track) => peer.addTrack(track, stream));
      peer.addEventListener('track', (event) => {
        const audio = new Audio();
        audio.autoplay = true;
        audio.srcObject = event.streams[0] ?? new MediaStream([event.track]);
        audioRef.current = audio;
        // Autoplay policy may block a detached element created outside a
        // user gesture; the speaking turn retries and falls back to the
        // next pointer interaction.
        void audio.play().catch(() => {});
      });

      // Speech recognition flows over this channel, so the session is only
      // truthfully 'listening' once it opens (or the peer is connected).
      const markListening = () => {
        if (connectTimerRef.current !== null) {
          window.clearTimeout(connectTimerRef.current);
          connectTimerRef.current = null;
        }
        setState((current) =>
          current === 'connecting' ? 'listening' : current
        );
      };

      const channel = peer.createDataChannel(DATA_CHANNEL_LABEL);
      channel.addEventListener('open', markListening);
      channel.addEventListener('message', (event) =>
        handleDataChannelMessage(String(event.data))
      );
      peer.addEventListener('connectionstatechange', () => {
        if (peer.connectionState === 'connected') {
          markListening();
        } else if (
          peer.connectionState === 'failed' ||
          peer.connectionState === 'disconnected'
        ) {
          fail('VOICE_SIGNALING_FAILED', 'peer connection lost');
        }
      });

      await peer.setLocalDescription(await peer.createOffer());
      // createOffer()'s SDP carries no ICE candidates; they are gathered
      // asynchronously after setLocalDescription. Because the relay is a
      // single request/response with no trickle path, wait for gathering and
      // send the local description that now includes the candidates.
      await waitForIceGathering(peer, ICE_GATHERING_TIMEOUT_MS);
      const response = await fetch(
        resolveUrl(`voice/sessions/${created.id}/sdp`, host),
        {
          method: 'POST',
          headers: jsonHeaders(true),
          credentials: 'include',
          body: JSON.stringify({ sdp_offer: peer.localDescription?.sdp ?? '' })
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
      // The connection may already have completed during setup; otherwise
      // wait for the data channel/peer to connect (via the listeners above)
      // and fail honestly if it never does, rather than capturing nothing
      // behind a listening UI.
      if (
        channel.readyState === 'open' ||
        peer.connectionState === 'connected'
      ) {
        markListening();
      } else {
        connectTimerRef.current = window.setTimeout(() => {
          connectTimerRef.current = null;
          if (
            channel.readyState !== 'open' &&
            peer.connectionState !== 'connected'
          ) {
            fail('VOICE_SIGNALING_FAILED', 'media path did not connect');
          }
        }, MEDIA_CONNECT_TIMEOUT_MS);
      }
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
      submitQueueRef.current = [];
      pendingConfirmRef.current = null;
      setPendingConfirm(null);
      releaseMedia();
      setPartial(null);
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
    pendingConfirm,
    confirmPending,
    discardPending
  };
}
