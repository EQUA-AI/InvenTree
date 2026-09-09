/**
 * Shared mocked-voice harness for the voice Playwright suites (P0-13).
 *
 * Mirrors the shape of aichat_harness.ts: one init script that installs a
 * controllable WebRTC/media surface, one set of route mocks, one request
 * observer. Unlike the original inline mocks in pui_voice_live.spec.ts the
 * data channel keeps a REAL listener registry, so tests can deliver
 * transcript, barge-in and playback events into the live hook with
 * `emitTranscript()` and friends. Nothing here reaches a real provider.
 *
 * Route precedence: Playwright matches the most recently registered route
 * first, so the generic `voice/sessions/*` handler is registered before the
 * more specific `/sdp`, `/turns`, `/cancel`, `/decision`, `/prompts` ones.
 */

import type { Page, Route } from '@playwright/test';

import {
  type ObservedRequest,
  observeRequest,
  openChat
} from './aichat_harness.js';

export { openChat };

export const mockSessionId = '22222222-2222-2222-2222-222222222222';
export const mockThreadId = 'thread_mocked';

export interface VoiceCapabilityPayload {
  enabled: boolean;
  webrtc: boolean;
  relay: boolean;
  confidence_floor?: number;
  [extra: string]: unknown;
}

export interface VoiceHarnessOptions {
  /** Capability body served for `GET /api/ai/voice/capability`. */
  capability?: VoiceCapabilityPayload;
  /**
   * Mock the session endpoints (`POST /sessions`, `/sdp`, `/turns`, ...).
   * When false only the capability route is mocked and the real backend
   * answers session creation (used to prove honest server rejection).
   */
  mockSessions?: boolean;
  /** Session id returned by the mocked `POST /sessions`. */
  sessionId?: string;
  /** Thread id carried by the mocked session and turn responses. */
  threadId?: string;
  /** Deny `getUserMedia` (microphone permission denied). */
  denyMicrophone?: boolean;
  /** Start with `HTMLMediaElement.play()` rejecting (autoplay blocked). */
  autoplayBlocked?: boolean;
  /**
   * Scripted turn responses. Receives the observed turn request body and
   * returns the JSON body for `POST /sessions/{id}/turns`. Defaults to a
   * completed, unspoken answer.
   */
  onTurn?: (body: Record<string, unknown>, index: number) => unknown;
  /** Scripted decision read (`GET /sessions/{id}/decision`). */
  onDecisionRead?: () => unknown;
  /** Scripted decision action (`POST /sessions/{id}/decision/{action}`). */
  onDecisionAction?: (action: string, body: Record<string, unknown>) => unknown;
  /** Scripted prompt request (`POST /sessions/{id}/prompts`). */
  onPrompt?: (body: Record<string, unknown>) => unknown;
  /** Body for `GET /api/ai/voice/ice-servers`; a number fulfils with that status. */
  iceServers?: unknown | number;
}

export interface VoiceObservations {
  sessionCreates: ObservedRequest[];
  sdpOffers: ObservedRequest[];
  turns: ObservedRequest[];
  cancels: ObservedRequest[];
  deletes: ObservedRequest[];
  decisionReads: ObservedRequest[];
  decisionActions: ObservedRequest[];
  prompts: ObservedRequest[];
  iceRequests: ObservedRequest[];
  /** True once the client issued `DELETE /sessions/{id}`. */
  readonly sessionEnded: boolean;
}

export const defaultCapability: VoiceCapabilityPayload = {
  enabled: true,
  webrtc: true,
  relay: false,
  confidence_floor: 0.85
};

export function sessionPayload(
  overrides: Record<string, unknown> = {}
): Record<string, unknown> {
  return {
    id: mockSessionId,
    state: 'created',
    thread_id: mockThreadId,
    transport: null,
    transports_allowed: { webrtc: true, relay: false },
    webrtc_preview: true,
    turn_count: 0,
    policy_version: 'test',
    terminal_reason: null,
    analysis_scope_version: 0,
    ...overrides
  };
}

export function turnPayload(
  overrides: Record<string, unknown> = {}
): Record<string, unknown> {
  return {
    session_id: mockSessionId,
    thread_id: mockThreadId,
    turn_id: 'turn-mocked',
    message: 'Mocked answer',
    workflow_used: 'general',
    response_state: 'complete',
    replayed: false,
    spoken: null,
    pending_question: null,
    ...overrides
  };
}

/**
 * Install the browser-side mocks. Must run before the page that hosts the
 * drawer loads (call it, then `page.reload()` like the original spec).
 */
export async function installVoiceMocks(
  page: Page,
  options: VoiceHarnessOptions = {}
): Promise<VoiceObservations> {
  const capability = options.capability ?? defaultCapability;
  const mockSessions = options.mockSessions ?? true;
  const sessionId = options.sessionId ?? mockSessionId;
  const threadId = options.threadId ?? mockThreadId;
  let sessionEnded = false;

  const observations: VoiceObservations = {
    sessionCreates: [],
    sdpOffers: [],
    turns: [],
    cancels: [],
    deletes: [],
    decisionReads: [],
    decisionActions: [],
    prompts: [],
    iceRequests: [],
    get sessionEnded() {
      return sessionEnded;
    }
  };

  await page.addInitScript(
    ({ denyMicrophone, autoplayBlocked }) => {
      type Listener = (event: any) => void;

      class ListenerRegistry {
        private listeners = new Map<string, Set<Listener>>();
        addEventListener(type: string, listener: Listener) {
          if (!this.listeners.has(type)) {
            this.listeners.set(type, new Set());
          }
          this.listeners.get(type)?.add(listener);
        }
        removeEventListener(type: string, listener: Listener) {
          this.listeners.get(type)?.delete(listener);
        }
        dispatch(type: string, event: any = {}) {
          for (const listener of this.listeners.get(type) ?? []) {
            listener({ type, ...event });
          }
        }
      }

      const mock = {
        channels: [] as MockDataChannel[],
        peers: [] as MockPeerConnection[],
        trackStopped: false,
        trackEnabled: true,
        autoplayBlocked,
        playAttempts: 0,
        wakeLockRequests: 0,
        /** Deliver a provider event over every open data channel. */
        emit(type: string, payload: Record<string, unknown> = {}) {
          const data = JSON.stringify({ type, ...payload });
          for (const channel of this.channels) {
            channel.dispatch('message', { data });
          }
        },
        setPeerState(state: string) {
          for (const peer of this.peers) {
            peer.connectionState = state;
            peer.dispatch('connectionstatechange');
          }
        },
        setAutoplayBlocked(blocked: boolean) {
          this.autoplayBlocked = blocked;
        },
        emitDeviceChange() {
          navigator.mediaDevices.dispatchEvent(new Event('devicechange'));
        }
      };

      class MockDataChannel extends ListenerRegistry {
        readyState = 'open';
        label: string;
        constructor(label: string) {
          super();
          this.label = label;
          mock.channels.push(this);
        }
        close() {
          this.readyState = 'closed';
        }
      }

      class MockPeerConnection extends ListenerRegistry {
        connectionState = 'connected';
        iceGatheringState = 'complete';
        localDescription = { type: 'offer', sdp: 'v=0\r\nmock-offer' };
        constructor() {
          super();
          mock.peers.push(this);
        }
        addTrack() {}
        createDataChannel(label: string) {
          return new MockDataChannel(label);
        }
        async createOffer() {
          return { type: 'offer', sdp: 'v=0\r\nmock-offer' };
        }
        async setLocalDescription() {}
        async setRemoteDescription() {}
        close() {
          this.connectionState = 'closed';
        }
      }

      const track = {
        kind: 'audio',
        get enabled() {
          return mock.trackEnabled;
        },
        set enabled(value: boolean) {
          mock.trackEnabled = value;
        },
        stop: () => {
          mock.trackStopped = true;
          (window as any).__voiceTrackStopped = true;
        }
      };

      const mediaDevices = new EventTarget() as EventTarget & {
        getUserMedia: () => Promise<unknown>;
        enumerateDevices: () => Promise<unknown[]>;
      };
      mediaDevices.getUserMedia = async () => {
        if (denyMicrophone) {
          throw new DOMException('Permission denied', 'NotAllowedError');
        }
        return {
          getTracks: () => [track],
          getAudioTracks: () => [track]
        };
      };
      mediaDevices.enumerateDevices = async () => [
        { kind: 'audioinput', deviceId: 'mic-1', label: 'Mock microphone' },
        { kind: 'audiooutput', deviceId: 'spk-1', label: 'Mock speaker' }
      ];
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: mediaDevices
      });

      Object.defineProperty(navigator, 'wakeLock', {
        configurable: true,
        value: {
          request: async () => {
            mock.wakeLockRequests += 1;
            return { released: false, release: async () => {} };
          }
        }
      });

      const originalPlay = HTMLMediaElement.prototype.play;
      HTMLMediaElement.prototype.play = function play(this: HTMLMediaElement) {
        mock.playAttempts += 1;
        if (mock.autoplayBlocked) {
          return Promise.reject(
            new DOMException('play() blocked', 'NotAllowedError')
          );
        }
        // A detached element with no real source: resolve instead of
        // surfacing jsdom-style media errors.
        try {
          return originalPlay.call(this).catch(() => undefined);
        } catch {
          return Promise.resolve();
        }
      };

      (window as any).RTCPeerConnection = MockPeerConnection;
      (window as any).__voiceMock = mock;
    },
    {
      denyMicrophone: options.denyMicrophone ?? false,
      autoplayBlocked: options.autoplayBlocked ?? false
    }
  );

  await page.route('**/api/ai/voice/capability', async (route: Route) => {
    await route.fulfill({ status: 200, json: capability });
  });

  if (!mockSessions) {
    return observations;
  }

  await page.route('**/api/ai/voice/ice-servers', async (route: Route) => {
    observations.iceRequests.push(await observeRequest(route.request()));
    if (typeof options.iceServers === 'number') {
      await route.fulfill({
        status: options.iceServers,
        json: { detail: 'unavailable' }
      });
      return;
    }
    await route.fulfill({
      status: 200,
      json: options.iceServers ?? { ice_servers: [], ttl_s: 60 }
    });
  });

  await page.route('**/api/ai/voice/sessions', async (route: Route) => {
    observations.sessionCreates.push(await observeRequest(route.request()));
    await route.fulfill({
      status: 201,
      json: sessionPayload({ id: sessionId, thread_id: threadId })
    });
  });

  await page.route('**/api/ai/voice/sessions/*', async (route: Route) => {
    const request = route.request();
    if (request.method() === 'DELETE') {
      sessionEnded = true;
      observations.deletes.push(await observeRequest(request));
      await route.fulfill({
        status: 200,
        json: { id: sessionId, state: 'ended' }
      });
      return;
    }
    await route.fulfill({
      status: 200,
      json: sessionPayload({ id: sessionId, thread_id: threadId })
    });
  });

  await page.route('**/api/ai/voice/sessions/*/sdp', async (route: Route) => {
    observations.sdpOffers.push(await observeRequest(route.request()));
    await route.fulfill({
      status: 200,
      json: { sdp_answer: 'v=0\r\nmock-answer' }
    });
  });

  await page.route(
    '**/api/ai/voice/sessions/*/cancel',
    async (route: Route) => {
      observations.cancels.push(await observeRequest(route.request()));
      await route.fulfill({
        status: 200,
        json: { id: sessionId, canceled_utterances: 0 }
      });
    }
  );

  await page.route('**/api/ai/voice/sessions/*/turns', async (route: Route) => {
    const observed = await observeRequest(route.request());
    observations.turns.push(observed);
    const index = observations.turns.length - 1;
    const body = options.onTurn
      ? options.onTurn(observed.body ?? {}, index)
      : turnPayload({ session_id: sessionId, thread_id: threadId });
    await route.fulfill({ status: 200, json: body });
  });

  await page.route(
    '**/api/ai/voice/sessions/*/prompts',
    async (route: Route) => {
      const observed = await observeRequest(route.request());
      observations.prompts.push(observed);
      await route.fulfill({
        status: 200,
        json: options.onPrompt
          ? options.onPrompt(observed.body ?? {})
          : { utterance_id: 'utt-prompt', playback_state: 'requested' }
      });
    }
  );

  await page.route(
    '**/api/ai/voice/sessions/*/decision',
    async (route: Route) => {
      observations.decisionReads.push(await observeRequest(route.request()));
      await route.fulfill({
        status: 200,
        json: options.onDecisionRead
          ? options.onDecisionRead()
          : { pending_decision: null, decision_event: null }
      });
    }
  );

  await page.route(
    '**/api/ai/voice/sessions/*/decision/*',
    async (route: Route) => {
      const observed = await observeRequest(route.request());
      observations.decisionActions.push(observed);
      const action = new URL(observed.url).pathname.split('/').pop() ?? '';
      await route.fulfill({
        status: 200,
        json: options.onDecisionAction
          ? options.onDecisionAction(action, observed.body ?? {})
          : { pending_decision: null, decision_event: null }
      });
    }
  );

  return observations;
}

export interface TranscriptEvent {
  text: string;
  itemId: string;
  confidence?: number | null;
  language?: string;
}

/** Deliver a final transcript (the provider's completed transcription event). */
export async function emitTranscript(page: Page, event: TranscriptEvent) {
  await page.evaluate(
    ({ text, itemId, confidence, language }) => {
      (window as any).__voiceMock.emit(
        'conversation.item.input_audio_transcription.completed',
        {
          transcript: text,
          item_id: itemId,
          ...(confidence === undefined ? {} : { confidence }),
          language: language ?? 'en-US'
        }
      );
    },
    {
      text: event.text,
      itemId: event.itemId,
      confidence: event.confidence,
      language: event.language
    }
  );
}

/** Deliver a partial (delta) transcript. */
export async function emitPartial(page: Page, event: TranscriptEvent) {
  await page.evaluate(
    ({ text, itemId }) => {
      (window as any).__voiceMock.emit(
        'conversation.item.input_audio_transcription.delta',
        { delta: text, item_id: itemId }
      );
    },
    { text: event.text, itemId: event.itemId }
  );
}

/** The technician started talking (barge-in). */
export async function emitSpeechStarted(page: Page) {
  await page.evaluate(() => {
    (window as any).__voiceMock.emit('input_audio_buffer.speech_started');
  });
}

/** Provider playback for the current response drained. */
export async function emitResponseDone(page: Page) {
  await page.evaluate(() => {
    (window as any).__voiceMock.emit('response.done');
  });
}

/** Provider-side error event. */
export async function emitProviderError(page: Page, message: string) {
  await page.evaluate((text) => {
    (window as any).__voiceMock.emit('error', { error: { message: text } });
  }, message);
}

/** Flip the mocked peer connection state (fires connectionstatechange). */
export async function setPeerState(page: Page, state: string) {
  await page.evaluate((next) => {
    (window as any).__voiceMock.setPeerState(next);
  }, state);
}

/** Make subsequent `play()` calls reject (or succeed again). */
export async function setAutoplayBlocked(page: Page, blocked: boolean) {
  await page.evaluate((next) => {
    (window as any).__voiceMock.setAutoplayBlocked(next);
  }, blocked);
}

/** Fire a `devicechange` on the mocked media devices. */
export async function emitDeviceChange(page: Page) {
  await page.evaluate(() => {
    (window as any).__voiceMock.emitDeviceChange();
  });
}

/** Snapshot of the browser-side mock counters. */
export async function readMockState(page: Page): Promise<{
  trackStopped: boolean;
  trackEnabled: boolean;
  playAttempts: number;
  wakeLockRequests: number;
  channels: number;
}> {
  return page.evaluate(() => {
    const mock = (window as any).__voiceMock;
    return {
      trackStopped: Boolean(mock.trackStopped),
      trackEnabled: Boolean(mock.trackEnabled),
      playAttempts: Number(mock.playAttempts),
      wakeLockRequests: Number(mock.wakeLockRequests),
      channels: mock.channels.length
    };
  });
}
