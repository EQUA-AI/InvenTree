import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { type StoreApi, createStore } from 'zustand/vanilla';
import type { VoiceDecisionStore } from '../../../states/VoiceDecisionState';
import { emptyDecisionState } from '../../../states/decisionReducer';
import type { ContextPreflight } from '../../aichat/contextPreflight';
import { VoiceSessionController } from './VoiceSessionController';
import {
  type VoiceCapability,
  type VoicePreferences,
  type VoiceSnapshot,
  initialVoiceSnapshot
} from './types';
import { isVoiceShortcut, voiceEscape } from './voiceShortcuts';

class FakeElement {
  editing = false;
  surface = false;
  closest(selector: string) {
    return selector === '[data-voice-surface]' ? this.surface : this.editing;
  }
}
class FakeAudio extends EventTarget {
  paused = true;
  autoplay = false;
  srcObject: unknown = null;
  play = vi.fn(async () => {
    this.paused = false;
  });
  pause = vi.fn(() => {
    this.paused = true;
  });
}
class FakeChannel extends EventTarget {
  readyState = 'open';
}
class FakePeer extends EventTarget {
  static peers: FakePeer[] = [];
  connectionState = 'connected';
  iceGatheringState = 'complete';
  localDescription = { sdp: 'offer' };
  channel = new FakeChannel();
  constructor() {
    super();
    FakePeer.peers.push(this);
  }
  createDataChannel() {
    return this.channel;
  }
  addTrack() {}
  close = vi.fn(() => {
    this.connectionState = 'closed';
  });
  createOffer = vi.fn(async () => ({ type: 'offer', sdp: 'offer' }));
  setLocalDescription = vi.fn(async () => {});
  setRemoteDescription = vi.fn(async () => {});
  getStats = vi.fn(async () => new Map());
}
const cap = {
  enabled: true,
  foreground_session: true,
  decisions: true,
  prompts: true,
  help: true,
  presentation: true,
  mobile_surface: true,
  wake_lock: false,
  ice_servers: [],
  modes: ['continuous', 'push_to_talk'],
  default_mode: 'continuous',
  max_queued_turns: 2,
  idle_timeout_s: 300,
  mic_silence_rms: 0.01,
  mic_silence_window_s: 1.5,
  route_loss_pause: true,
  tts_timeout_s: 8,
  decision_max_armed_s: 300,
  reconnect: { grace_s: 3, max_attempts: 2 },
  confidence_floor: 0.85,
  locales: ['en-US'],
  voices: [{ locale: 'en-US', voice: 'en-US-AvaNeural' }],
  default_locale: 'en-US',
  default_voice: 'en-US-AvaNeural',
  consent_version: 'consent-v3-memory',
  voice_write_locales: ['en-US']
} satisfies VoiceCapability;
let controller: VoiceSessionController;
let state: StoreApi<VoiceSnapshot>;
let decisions: StoreApi<VoiceDecisionStore>;
let doc: EventTarget & { hidden: boolean; cookie: string };
let track: EventTarget & { enabled: boolean; stop: ReturnType<typeof vi.fn> };
let prefs: VoicePreferences;
let requests: {
  path: string;
  method: string;
  body: Record<string, unknown> | null;
}[];
let fetcher: ReturnType<typeof vi.fn>;
const flush = async () => {
  await vi.advanceTimersByTimeAsync(0);
};
it('refuses startup while the configured voice runtime is unavailable, then recovers', async () => {
  controller.setCapability({
    ...cap,
    runtime: {
      state: 'transiently_unavailable',
      available: false,
      reason: 'provider_throttled',
      retry_after_s: 30
    }
  });
  await controller.start();
  expect(state.getState().state).toBe('unavailable');
  expect(requests).toEqual([]);
  expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  controller.setCapability({
    ...cap,
    runtime: {
      state: 'ready',
      available: true,
      reason: null,
      retry_after_s: null
    }
  });
  expect(state.getState().state).toBe('ready');
});
beforeEach(() => {
  vi.useFakeTimers();
  FakePeer.peers = [];
  const events = new EventTarget();
  vi.stubGlobal('window', {
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    addEventListener: events.addEventListener.bind(events),
    removeEventListener: events.removeEventListener.bind(events),
    dispatchEvent: events.dispatchEvent.bind(events)
  });
  doc = Object.assign(new EventTarget(), {
    hidden: false,
    cookie: 'csrftoken=test'
  });
  vi.stubGlobal('document', doc);
  vi.stubGlobal('Element', FakeElement);
  vi.stubGlobal('AudioContext', undefined);
  vi.stubGlobal('Audio', FakeAudio);
  vi.stubGlobal('RTCPeerConnection', FakePeer);
  track = Object.assign(new EventTarget(), { enabled: true, stop: vi.fn() });
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  const media = Object.assign(new EventTarget(), {
    getUserMedia: vi.fn(async () => stream),
    enumerateDevices: vi.fn(async () => [])
  });
  vi.stubGlobal('navigator', {
    mediaDevices: media,
    onLine: true,
    vibrate: vi.fn()
  });
  requests = [];
  fetcher = vi.fn(async (url: string, options: RequestInit) => {
    const path = new URL(url).pathname;
    requests.push({
      path,
      method: options.method ?? 'GET',
      body: options.body ? JSON.parse(String(options.body)) : null
    });
    const session = {
      id: 'session-1',
      thread_id: 'thread-1',
      state: 'active',
      locale: 'en-US',
      voice: 'en-US-AvaNeural',
      consent_version: 'consent-v3-memory',
      transports_allowed: { webrtc: true, relay: false }
    };
    const body = path.endsWith('/sdp')
      ? { sdp_answer: 'answer' }
      : path.endsWith('/prompts')
        ? {
            utterance_id: 'spoken-1',
            spoken_summary: 'Fixed prompt',
            spoken_summary_hash: 'hash',
            playback_state: 'requested'
          }
        : path.endsWith('/turns')
          ? {
              session_id: session.id,
              thread_id: session.thread_id,
              turn_id: 'turn-1',
              response_state: 'complete',
              spoken: null
            }
          : session;
    return { ok: true, json: async () => body };
  });
  vi.stubGlobal('fetch', fetcher);
  prefs = {
    voiceListeningMode: 'continuous',
    voiceEarcons: true,
    voiceSrAnnounceTranscripts: false,
    voiceConsentVersion: 'consent-v3-memory',
    voiceLocale: 'en-US',
    voiceOutputVoice: 'en-US-AvaNeural',
    voiceSpeakerNoticeSeen: false
  };
  state = createStore<VoiceSnapshot>(() => ({ ...initialVoiceSnapshot }));
  decisions = createStore<VoiceDecisionStore>(() => ({
    ...emptyDecisionState,
    setSession: vi.fn(),
    refresh: vi.fn(async () => {}),
    applyTurn: vi.fn(),
    decide: vi.fn(async () => {})
  }));
  controller = new VoiceSessionController(
    state,
    decisions,
    () => prefs,
    (values) => {
      prefs = { ...prefs, ...values };
    }
  );
  controller.configure('https://app.test/api/ai', 'thread-1');
  controller.setCapability(cap);
});
afterEach(async () => {
  await controller.end();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('reports browser-only lifecycle while accepting consecutive turns after failed speech', async () => {
  const respond = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (url: string, options: RequestInit) => {
    const response = await respond(url, options);
    if (!new URL(url).pathname.endsWith('/turns')) return response;
    const body = await response.json();
    return {
      ok: true,
      json: async () => ({
        ...body,
        spoken: {
          utterance_id: 'failed-output',
          spoken_summary: 'Hello',
          spoken_summary_hash: 'hash',
          playback_state: 'failed'
        }
      })
    };
  });
  await controller.start();
  for (let n = 0; n < 10; n++) {
    await controller.submitTranscript({
      text: 'Hello',
      itemId: `turn-${n}`,
      confidence: 1,
      language: 'en-US'
    });
    await flush();
    controller.handleEvent(
      JSON.stringify({
        type: 'response.created',
        response: { id: `resp-${n}`, status: 'in_progress' }
      })
    );
    controller.handleEvent(
      JSON.stringify({
        type: 'response.done',
        response: { id: `resp-${n}`, status: 'completed', output: ['private'] }
      })
    );
    await flush();
  }
  expect(requests.filter((r) => r.path.endsWith('/turns'))).toHaveLength(10);
  const observations = requests.filter((r) => r.path.endsWith('/lifecycle'));
  expect(observations).toHaveLength(20);
  expect(observations[1].body).toEqual({
    type: 'response.done',
    response_id: 'resp-0',
    status: 'completed'
  });
  expect(decisions.getState().decide).not.toHaveBeenCalled();
  expect(state.getState().transport).toBe('connected');
});

it.each([
  'Where is stock for 2.01 PCBA located?',
  'For 002.01-PCBA, what is the on-hand stock?'
])('holds a numeric-leading IPN until explicit review: %s', async (text) => {
  await controller.start();
  controller.handleEvent(
    JSON.stringify({
      type: 'conversation.item.input_audio_transcription.completed',
      transcript: text,
      item_id: 'ipn-review'
    })
  );
  await flush();
  expect(state.getState().pendingConfirm?.text).toBe(text);
  expect(requests.filter((r) => r.path.endsWith('/turns'))).toHaveLength(0);
  const corrected = 'Where is stock for 002.01-PCBA located?';
  controller.handleEvent(
    JSON.stringify({
      type: 'conversation.item.input_audio_transcription.completed',
      transcript: corrected,
      item_id: 'ipn-corrected'
    })
  );
  await flush();
  expect(state.getState().pendingConfirm?.revision).toBe(2);
  await controller.confirmPending();
  await flush();
  const turns = requests.filter((r) => r.path.endsWith('/turns'));
  expect(turns).toHaveLength(1);
  expect(turns[0].body?.transcript).toBe(corrected);
  expect(decisions.getState().decide).not.toHaveBeenCalled();
});

it('starts once, gates PTT tracks, and survives minimize', async () => {
  await Promise.all([controller.start(), controller.start()]);
  expect(
    requests.filter((r) => r.path.endsWith('/sessions') && r.method === 'POST')
  ).toHaveLength(1);
  expect(track.enabled).toBe(true);
  controller.minimize();
  expect(track.stop).not.toHaveBeenCalled();
  controller.setMode('push_to_talk');
  expect(track.enabled).toBe(false);
  controller.pushToTalk(true);
  expect(track.enabled).toBe(true);
  window.dispatchEvent(new Event('blur'));
  expect(track.enabled).toBe(false);
  controller.toggleMute();
  controller.pushToTalk(true);
  expect(track.enabled).toBe(false);
  expect(prefs.voiceListeningMode).toBe('push_to_talk');
});
it('legacy close mode and logout end media', async () => {
  controller.setCapability({ ...cap, foreground_session: false });
  await controller.start();
  controller.minimize();
  await flush();
  expect(track.stop).toHaveBeenCalled();
  expect(state.getState().session).toBeNull();
});
it('missing consent cannot mint a session or request the microphone', async () => {
  prefs.voiceConsentVersion = 'consent-v1';
  await controller.start();
  expect(requests).toHaveLength(0);
  expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  expect(state.getState().error?.code).toBe('VOICE_CONSENT_REQUIRED');
});
it('a stale null turn refreshes focus once without resubmitting', async () => {
  await controller.start();
  await flush();
  vi.mocked(decisions.getState().refresh).mockClear();
  fetcher.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      session_id: 'session-1',
      thread_id: 'thread-1',
      turn_id: 'stale-turn',
      response_state: 'complete',
      spoken: null,
      pending_decision: null,
      decision_event: { kind: 'stale', decision_id: null, sequence: null }
    })
  });
  await controller.submitTranscript({
    text: 'Read the whole note.',
    itemId: 'read-note',
    confidence: 1,
    language: 'en-US'
  });
  await flush();
  expect(decisions.getState().refresh).toHaveBeenCalledTimes(1);
  expect(
    fetcher.mock.calls.filter(([url]) => url.endsWith('/turns'))
  ).toHaveLength(1);
  expect(track.enabled).toBe(true);
});
it('hide stops capture immediately, sets aside and expires at the reported bound', async () => {
  await controller.start();
  doc.hidden = true;
  doc.dispatchEvent(new Event('visibilitychange'));
  expect(track.enabled).toBe(false);
  expect(state.getState().playback).toBe('paused');
  await flush();
  expect(requests.some((r) => r.path.endsWith('/suspend'))).toBe(true);
  await vi.advanceTimersByTimeAsync(299_999);
  expect(state.getState().session).not.toBeNull();
  await vi.advanceTimersByTimeAsync(1);
  expect(state.getState().session).toBeNull();
  expect(state.getState().notice).toBe('ended_away');
});
it('visible return refreshes and never sends assent or re-arms', async () => {
  await controller.start();
  doc.hidden = true;
  doc.dispatchEvent(new Event('visibilitychange'));
  await flush();
  doc.hidden = false;
  doc.dispatchEvent(new Event('visibilitychange'));
  await flush();
  expect(track.enabled).toBe(true);
  expect(decisions.getState().refresh).toHaveBeenCalled();
  expect(decisions.getState().decide).not.toHaveBeenCalled();
  expect(state.getState().notice).toBe('readback_available');
});
it('stop never waits for an application turn', async () => {
  await controller.start();
  fetcher.mockImplementationOnce(() => new Promise(() => {}));
  void controller.submitTranscript({
    text: 'hello',
    itemId: 'i1',
    confidence: 1,
    language: 'en-US'
  });
  controller.handleEvent(
    JSON.stringify({
      type: 'conversation.item.input_audio_transcription.completed',
      transcript: 'stop speaking',
      item_id: 'stop'
    })
  );
  expect(state.getState().playback).toBe('paused');
  await flush();
  expect(requests.some((r) => r.path.endsWith('/cancel'))).toBe(true);
});
it('speech timeout stays text-only and retry uses presentation, never turns', async () => {
  await controller.start();
  await controller.prompt('help');
  await vi.advanceTimersByTimeAsync(8000);
  expect(state.getState().notice).toBe('tts_timeout');
  state.setState({
    presentation: { id: 'p1', source_hash: 'h', index: 0, total: 2 }
  });
  await controller.resumeOutput();
  expect(requests.some((r) => r.path.endsWith('/presentation'))).toBe(true);
  expect(requests.some((r) => r.path.endsWith('/turns'))).toBe(false);
});
it.each(['before', 'after'] as const)(
  'ordinary output completion %s the HTTP result leaves the UI listening',
  async (ordering) => {
    await controller.start();
    let finish!: (response: unknown) => void;
    fetcher.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        })
    );
    await controller.submitTranscript({
      text: 'record a fault note',
      itemId: 'fault-1',
      confidence: 1,
      language: 'en-US'
    });
    const completeOutput = () => {
      for (const event of [
        { type: 'response.created', response: { id: 'ordinary-1' } },
        { type: 'output_audio_buffer.started', response_id: 'ordinary-1' },
        {
          type: 'response.audio_transcript.done',
          response_id: 'ordinary-1',
          transcript: 'Fault notes cannot be filed by voice yet.'
        },
        { type: 'response.audio.done', response_id: 'ordinary-1' },
        { type: 'output_audio_buffer.stopped' },
        {
          type: 'response.done',
          response: { id: 'ordinary-1', status: 'completed' }
        }
      ])
        controller.handleEvent(JSON.stringify(event));
    };
    if (ordering === 'before') completeOutput();
    finish({
      ok: true,
      json: async () => ({
        session_id: 'session-1',
        thread_id: 'thread-1',
        turn_id: 'turn-1',
        response_state: 'complete',
        pending_decision: null,
        spoken: {
          utterance_id: 'fault-utterance',
          spoken_summary: 'Fault notes cannot be filed by voice yet.',
          spoken_summary_hash: 'fault-hash',
          playback_state: 'requested'
        }
      })
    });
    await flush();
    if (ordering === 'after') completeOutput();
    expect(state.getState().playback).toBe('idle');
    expect(state.getState().state).toBe('listening');
    expect(decisions.getState().decide).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(8000);
    expect(state.getState().notice).not.toBe('tts_timeout');
    expect(requests.some((r) => r.path.endsWith('/cancel'))).toBe(false);
  }
);
it('reconnect creates a same-thread session without replaying the submitted item', async () => {
  await controller.start();
  await controller.submitTranscript({
    text: 'hello',
    itemId: 'i1',
    confidence: 1,
    language: 'en-US'
  });
  await flush();
  const peer = FakePeer.peers[0];
  peer.connectionState = 'failed';
  peer.dispatchEvent(new Event('connectionstatechange'));
  expect(track.enabled).toBe(false);
  await vi.advanceTimersByTimeAsync(3000);
  await flush();
  expect(requests.filter((r) => r.path.endsWith('/turns'))).toHaveLength(1);
  expect(
    requests
      .filter((r) => r.path.endsWith('/sessions') && r.method === 'POST')
      .map((r) => r.body?.thread_id)
  ).toEqual(['thread-1', 'thread-1']);
  expect(state.getState().muted).toBe(true);
  expect(track.enabled).toBe(false);
  controller.toggleMute();
  expect(state.getState().muted).toBe(false);
  expect(track.enabled).toBe(true);
});
it('quality estimates preserve a live read-back; a real interface handoff still pauses', async () => {
  const connection = Object.assign(new EventTarget(), {
    type: 'wifi',
    rtt: 50,
    downlink: 10,
    effectiveType: '4g'
  });
  Object.defineProperty(navigator, 'connection', { value: connection });
  await controller.start();
  await controller.prompt('help');
  controller.handleEvent(
    JSON.stringify({ type: 'output_audio_buffer.started' })
  );
  for (const change of [
    { rtt: 200 },
    { downlink: 1 },
    { effectiveType: '3g' }
  ]) {
    Object.assign(connection, change);
    connection.dispatchEvent(new Event('change'));
    await flush();
  }
  expect(state.getState().transport).toBe('connected');
  expect(track.enabled).toBe(true);
  expect(requests.filter((r) => r.path.endsWith('/suspend'))).toHaveLength(0);
  expect(decisions.getState().decide).not.toHaveBeenCalled();
  connection.type = 'cellular';
  connection.dispatchEvent(new Event('change'));
  expect(track.enabled).toBe(false);
  await vi.advanceTimersByTimeAsync(3000);
  expect(state.getState().transport).toBe('connected');
  expect(state.getState().muted).toBe(true);
  expect(requests.filter((r) => r.path.endsWith('/turns'))).toHaveLength(0);
  expect(
    requests.filter((r) => r.path.endsWith('/sessions') && r.method === 'POST')
  ).toHaveLength(1);
});
it('global shortcut and Escape leave typing and barcode inputs alone', () => {
  const target = new FakeElement();
  const event = {
    key: 'V',
    repeat: false,
    altKey: false,
    ctrlKey: true,
    metaKey: false,
    shiftKey: true,
    target
  } as unknown as KeyboardEvent;
  expect(isVoiceShortcut(event)).toBe(true);
  target.editing = true;
  expect(isVoiceShortcut(event)).toBe(false);
  target.editing = false;
  target.surface = true;
  expect(voiceEscape({ ...event, key: 'Escape' } as KeyboardEvent, true)).toBe(
    'stop'
  );
  expect(voiceEscape({ ...event, key: 'Escape' } as KeyboardEvent, false)).toBe(
    'minimize'
  );
  expect(isVoiceShortcut({ ...event, key: 'M' } as KeyboardEvent)).toBe(false);
  expect(isVoiceShortcut({ ...event, repeat: true } as KeyboardEvent)).toBe(
    false
  );
});

it('visible idle announces before expiry and ends at exactly 300 seconds', async () => {
  await controller.start();
  await vi.advanceTimersByTimeAsync(298_000);
  expect(requests.some((r) => r.body?.status === 'session_ended')).toBe(true);
  controller.handleEvent(
    JSON.stringify({ type: 'output_audio_buffer.started' })
  );
  await vi.advanceTimersByTimeAsync(1999);
  expect(state.getState().session).not.toBeNull();
  await vi.advanceTimersByTimeAsync(1);
  expect(state.getState().session).toBeNull();
  expect(state.getState().notice).toBe('idle_ended');
});

it.each(['help', 'presentation'] as const)(
  'late %s cannot resume after Stop',
  async (kind) => {
    await controller.start();
    state.setState({
      presentation: { id: 'p1', source_hash: 'h', index: 0, total: 2 }
    });
    let finish!: (response: unknown) => void;
    fetcher.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        })
    );
    const pending =
      kind === 'help' ? controller.prompt('help') : controller.present('next');
    await controller.cancel();
    finish({
      ok: true,
      json: async () => ({
        utterance_id: 'late',
        spoken_summary: 'Late answer',
        playback_state: 'requested',
        presentation: { id: 'p1', source_hash: 'h', index: 1, total: 2 },
        spoken: {
          utterance_id: 'late',
          spoken_summary: 'Late answer',
          playback_state: 'requested'
        }
      })
    });
    await pending;
    expect(state.getState().playback).toBe('paused');
    expect(state.getState().presentation?.index).toBe(0);
    expect(state.getState().lastSpoken).toBeNull();
  }
);

it('reconnect failures stop after two attempts and never resend turns', async () => {
  await controller.start();
  const peer = FakePeer.peers[0];
  const original = fetcher.getMockImplementation()!;
  let attempts = 0;
  fetcher.mockImplementation((url: string, options: RequestInit) => {
    if (url.endsWith('/sessions') && options.method === 'POST') {
      attempts++;
      return Promise.reject(new Error('unavailable'));
    }
    return original(url, options);
  });
  peer.connectionState = 'failed';
  peer.dispatchEvent(new Event('connectionstatechange'));
  await vi.advanceTimersByTimeAsync(30_000);
  expect(attempts).toBe(2);
  expect(state.getState().transport).toBe('failed');
  expect(state.getState().session).toBeNull();
  expect(requests.some((r) => r.path.endsWith('/turns'))).toBe(false);
});

it('provider speech-start preserves the Phase B shared-speaker echo guard', async () => {
  await controller.start();
  await controller.prompt('help');
  const audio = new FakeAudio();
  const peer = FakePeer.peers[0];
  vi.stubGlobal(
    'Audio',
    vi.fn(() => audio)
  );
  peer.dispatchEvent(
    Object.assign(new Event('track'), { streams: [{}], track: {} })
  );
  audio.pause.mockClear();
  controller.handleEvent(
    JSON.stringify({ type: 'input_audio_buffer.speech_started' })
  );
  expect(audio.pause).not.toHaveBeenCalled();
  expect(track.enabled).toBe(true);
});

it('blocked autoplay keeps capture alive with a visible text fallback', async () => {
  await controller.start();
  const audio = new FakeAudio();
  audio.play.mockRejectedValue(new DOMException('blocked', 'NotAllowedError'));
  vi.stubGlobal(
    'Audio',
    vi.fn(() => audio)
  );
  FakePeer.peers[0].dispatchEvent(
    Object.assign(new Event('track'), { streams: [{}], track: {} })
  );
  await controller.prompt('help');
  await flush();
  expect(state.getState().playback).toBe('blocked');
  expect(state.getState().notice).toBe('audio_blocked');
  expect(state.getState().lastSpoken?.spoken_summary).toBe('Fixed prompt');
  expect(track.enabled).toBe(true);
  expect(requests.some((r) => r.path.endsWith('/turns'))).toBe(false);
});

it('detectable output loss cancels the server and never repeats a business turn', async () => {
  vi.mocked(navigator.mediaDevices.enumerateDevices).mockResolvedValueOnce([
    {
      kind: 'audiooutput',
      deviceId: 'headset',
      label: 'Headset'
    } as MediaDeviceInfo
  ]);
  await controller.start();
  navigator.mediaDevices.dispatchEvent(new Event('devicechange'));
  await flush();
  expect(requests.some((r) => r.path.endsWith('/cancel'))).toBe(true);
  expect(requests.some((r) => r.body?.status === 'route_changed')).toBe(true);
  expect(state.getState().notice).toBe('route_changed');
  expect(requests.some((r) => r.path.endsWith('/turns'))).toBe(false);
});

it('stops a late permission grant and never overlaps a pending acquisition', async () => {
  let grant!: (value: MediaStream) => void;
  vi.mocked(navigator.mediaDevices.getUserMedia).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        grant = resolve;
      })
  );
  const starting = controller.start();
  await flush();
  expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
  await controller.end();
  await controller.start();
  expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
  grant({ getTracks: () => [track] } as unknown as MediaStream);
  await starting;
  expect(track.stop).toHaveBeenCalledTimes(1);
  expect(state.getState().session).toBeNull();
  expect(FakePeer.peers).toHaveLength(0);
  await controller.start();
  expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(2);
});

it('an active session is fenced and ended when the visible thread switches', async () => {
  await controller.start();
  expect(state.getState().session?.thread_id).toBe('thread-1');
  controller.configure('https://app.test/api/ai', 'thread-2');
  await controller.submitTranscript({
    text: 'What is its status?',
    itemId: 'late-item',
    confidence: 1,
    language: 'en-US'
  });
  // Safe minimum contract (planning probe promoted): block/end until the new
  // thread binding is confirmed. No silent rebind, no replayed turn.
  expect(
    requests.filter((r) => r.path.endsWith('/sessions/session-1/turns'))
  ).toHaveLength(0);
  expect(state.getState().session).toBeNull();
  // The old session is torn down server-side, not left running on thread-1.
  expect(
    requests.filter(
      (r) => r.path.endsWith('/sessions/session-1') && r.method === 'DELETE'
    )
  ).toHaveLength(1);
});

it('an unchanged visible thread still submits to its own active session', async () => {
  await controller.start();
  controller.configure('https://app.test/api/ai', 'thread-1');
  await controller.submitTranscript({
    text: 'What is its status?',
    itemId: 'control-item',
    confidence: 1,
    language: 'en-US'
  });
  expect(
    requests.filter((r) => r.path.endsWith('/sessions/session-1/turns'))
  ).toHaveLength(1);
});

it('a session created while the visible thread switched is discarded, never adopted', async () => {
  let releaseCreate!: (value: unknown) => void;
  fetcher.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        releaseCreate = resolve;
      })
  );
  const starting = controller.start();
  await flush();
  // The visible thread switches while the session is still being created.
  controller.configure('https://app.test/api/ai', 'thread-2');
  releaseCreate({
    ok: true,
    status: 200,
    json: async () => ({
      id: 'session-1',
      thread_id: 'thread-1',
      state: 'active',
      locale: 'en-US',
      voice: 'en-US-AvaNeural',
      consent_version: 'consent-v3-memory',
      transports_allowed: { webrtc: true, relay: false }
    })
  });
  await starting;
  // The stale start is deleted server-side and never becomes the live session.
  expect(state.getState().session).toBeNull();
  expect(
    requests.filter(
      (r) => r.path.endsWith('/sessions/session-1') && r.method === 'DELETE'
    )
  ).toHaveLength(1);
});

/** Fresh controller wired with an injected shared context preflight. */
const withPreflight = (preflight: ContextPreflight) => {
  const local = new VoiceSessionController(
    state,
    decisions,
    () => prefs,
    (values) => {
      prefs = { ...prefs, ...values };
    },
    preflight
  );
  local.configure('https://app.test/api/ai', 'thread-1');
  local.setCapability(cap);
  return local;
};

it('a failed context preflight blocks voice start instead of creating an unscoped session', async () => {
  const local = withPreflight(async () => ({
    ok: false,
    code: 'scope_update_rejected'
  }));
  await local.start();
  expect(requests).toEqual([]);
  expect(state.getState().session).toBeNull();
  expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
});

it('voice start binds the authoritative thread returned by the context preflight', async () => {
  const local = withPreflight(async () => ({ ok: true, threadId: 'thread-9' }));
  await local.start();
  const creates = requests.filter(
    (r) => r.path.endsWith('/sessions') && r.method === 'POST'
  );
  expect(creates).toHaveLength(1);
  expect(creates[0].body?.thread_id).toBe('thread-9');
  await local.end();
});

it('a preflight resolving after a thread switch can never admit the start', async () => {
  let releasePreflight!: (value: { ok: true; threadId: string | null }) => void;
  const local = withPreflight(
    () =>
      new Promise((resolve) => {
        releasePreflight = resolve;
      })
  );
  const starting = local.start();
  await flush();
  // The visible thread switches while the context preflight is still running.
  local.configure('https://app.test/api/ai', 'thread-2');
  releasePreflight({ ok: true, threadId: 'thread-1' });
  await starting;
  expect(requests).toEqual([]);
  expect(state.getState().session).toBeNull();
});

// ---------------------------------------------------------------------------
// plan C — conversation-focus stamps and safe live focus transitions

const turnOk = {
  ok: true,
  status: 200,
  json: async () => ({
    session_id: 'session-1',
    thread_id: 'thread-1',
    turn_id: 'turn-1',
    response_state: 'complete',
    spoken: null
  })
};
const transcript = (itemId: string, text = 'what is its status?') => ({
  text,
  itemId,
  confidence: 1,
  language: 'en-US'
});

/** Fresh controller wired with an injected thread-focus stamp source. */
const withFocus = (focusStamp: () => number | null) => {
  const local = new VoiceSessionController(
    state,
    decisions,
    () => prefs,
    (values) => {
      prefs = { ...prefs, ...values };
    },
    undefined,
    focusStamp
  );
  local.configure('https://app.test/api/ai', 'thread-1');
  local.setCapability(cap);
  return local;
};

/** One-off fetch response that still records the request like the default. */
const recordOnce = (handler: () => unknown) => {
  fetcher.mockImplementationOnce(async (url: string, options: RequestInit) => {
    requests.push({
      path: new URL(url).pathname,
      method: options.method ?? 'GET',
      body: options.body ? JSON.parse(String(options.body)) : null
    });
    return handler();
  });
};

it('stamps each utterance at capture, not at later submission', async () => {
  let stamp: number | null = 3;
  const local = withFocus(() => stamp);
  await local.start();
  let releaseTurn!: (value: unknown) => void;
  // The first turn POST is held in flight so the second utterance stays
  // QUEUED (captured) while the visible focus advances underneath it.
  recordOnce(
    () =>
      new Promise((resolve) => {
        releaseTurn = resolve;
      })
  );
  void local.submitTranscript(transcript('item-1', 'first'));
  await flush();
  void local.submitTranscript(transcript('item-2', 'second'));
  await flush();
  // A confirmed focus change lands BEFORE the queued utterance submits.
  stamp = 5;
  releaseTurn(turnOk);
  for (let i = 0; i < 10; i++) await flush();
  const turns = requests.filter((r) => r.path.endsWith('/turns'));
  expect(turns).toHaveLength(2);
  // Capture-time stamps: the queued words are never rebound to the newer
  // focus version that appeared while they waited.
  expect(turns.map((r) => r.body?.expected_focus_version)).toEqual([3, 3]);
  await local.end();
});

it('omits the focus stamp when no version was ever observed', async () => {
  const local = withFocus(() => null);
  await local.start();
  const create = requests.find(
    (r) => r.path.endsWith('/sessions') && r.method === 'POST'
  );
  expect(create?.body).not.toHaveProperty('expected_focus_version');
  await local.submitTranscript(transcript('item-1'));
  for (let i = 0; i < 5; i++) await flush();
  const turn = requests.find((r) => r.path.endsWith('/turns'));
  expect(turn?.body).not.toHaveProperty('expected_focus_version');
  await local.end();
});

it('stamps session creation with the observed focus version', async () => {
  const local = withFocus(() => 2);
  await local.start();
  const create = requests.find(
    (r) => r.path.endsWith('/sessions') && r.method === 'POST'
  );
  expect(create?.body?.expected_focus_version).toBe(2);
  await local.end();
});

it('a stale focus stamp keeps the session alive and holds the utterance for review', async () => {
  let stamp: number | null = 3;
  const local = withFocus(() => stamp);
  const staleEvents: string[] = [];
  local.listeners.add({ onFocusStale: () => staleEvents.push('focus-stale') });
  await local.start();
  recordOnce(async () => ({
    ok: false,
    status: 409,
    json: async () => ({ detail: 'VOICE_FOCUS_CHANGED' })
  }));
  await local.submitTranscript(transcript('item-1'));
  for (let i = 0; i < 5; i++) await flush();
  // Focus is not scope: the session and transport stay alive.
  expect(state.getState().session).not.toBeNull();
  expect(
    requests.filter(
      (r) => r.path.endsWith('/sessions/session-1') && r.method === 'DELETE'
    )
  ).toHaveLength(0);
  // The refused utterance was NOT recorded: it is held for user review and
  // never automatically retried.
  expect(staleEvents).toEqual(['focus-stale']);
  expect(state.getState().pendingConfirm?.itemId).toBe('item-1');
  expect(state.getState().queued).toBe(0);
  expect(requests.filter((r) => r.path.endsWith('/turns'))).toHaveLength(1);
  // An explicit user confirm re-captures with the CURRENT focus version.
  stamp = 6;
  await local.confirmPending();
  for (let i = 0; i < 5; i++) await flush();
  const turns = requests.filter((r) => r.path.endsWith('/turns'));
  expect(turns).toHaveLength(2);
  expect(turns[1].body?.expected_focus_version).toBe(6);
  await local.end();
});

it('a stale focus stamp at session create refuses the start before it begins', async () => {
  const stamp: number | null = 3;
  const local = withFocus(() => stamp);
  const staleEvents: string[] = [];
  local.listeners.add({ onFocusStale: () => staleEvents.push('focus-stale') });
  recordOnce(async () => ({
    ok: false,
    status: 409,
    json: async () => ({ detail: 'VOICE_FOCUS_CHANGED' })
  }));
  await local.start();
  expect(state.getState().session).toBeNull();
  expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  expect(staleEvents).toEqual(['focus-stale']);
  expect(state.getState().error?.code).toBe('VOICE_FOCUS_CHANGED');
  // No retry: the focus must be re-confirmed through the owner focus PUT.
  expect(
    requests.filter((r) => r.path.endsWith('/sessions') && r.method === 'POST')
  ).toHaveLength(1);
});

it('a confirmed focus change discards queued context and fences late results without ending the session', async () => {
  const local = withFocus(() => 3);
  const turns: string[] = [];
  local.listeners.add({
    onTurnResult: (turn) => turns.push(turn.turn_id)
  });
  await local.start();
  let releaseTurn!: (value: unknown) => void;
  recordOnce(
    () =>
      new Promise((resolve) => {
        releaseTurn = resolve;
      })
  );
  void local.submitTranscript(transcript('item-1', 'first'));
  await flush();
  void local.submitTranscript(transcript('item-2', 'second'));
  await flush();
  // Manual chip Change while both are outstanding.
  local.focusChanged();
  expect(state.getState().session).not.toBeNull();
  expect(state.getState().queued).toBe(0);
  releaseTurn(turnOk);
  for (let i = 0; i < 10; i++) await flush();
  // The queued pre-change utterance is discarded (never silently rebound)
  // and the in-flight result never reaches UI callbacks.
  expect(requests.filter((r) => r.path.endsWith('/turns'))).toHaveLength(1);
  expect(turns).toHaveLength(0);
  expect(state.getState().lastSubmitted?.status).toBe('unconfirmed');
  // Fresh capture after the confirmed focus ack uses the new stamp.
  await local.submitTranscript(transcript('item-3', 'third'));
  for (let i = 0; i < 5; i++) await flush();
  const submitted = requests.filter((r) => r.path.endsWith('/turns'));
  expect(submitted).toHaveLength(2);
  await local.end();
});

it('bridges the local progress preference into the turn payload', async () => {
  prefs = { ...prefs, voiceProgressMode: 'guided' };
  const local = withFocus(() => null);
  await local.start();
  await local.submitTranscript(transcript('item-1'));
  for (let i = 0; i < 5; i++) await flush();
  const turn = requests.find((r) => r.path.endsWith('/turns'));
  expect(turn?.body?.progress_mode).toBe('guided');
  await local.end();
});

it('measures the client queue wait from enqueue to actual submit', async () => {
  let releaseTurn!: (value: unknown) => void;
  let held = true;
  fetcher.mockImplementation(async (url: string, options: RequestInit) => {
    const path = new URL(url).pathname;
    requests.push({
      path,
      method: options.method ?? 'GET',
      body: options.body ? JSON.parse(String(options.body)) : null
    });
    if (path.endsWith('/timing-epoch'))
      return { ok: true, json: async () => ({ epoch: 'a'.repeat(32) }) };
    if (path.endsWith('/turns') && held)
      return new Promise((resolve) => {
        releaseTurn = () => resolve({ ok: true, json: async () => turnOk });
      });
    if (path.endsWith('/turns')) return { ok: true, json: async () => turnOk };
    if (path.endsWith('/sdp'))
      return { ok: true, json: async () => ({ sdp_answer: 'answer' }) };
    return {
      ok: true,
      json: async () => ({
        id: 'session-1',
        thread_id: 'thread-1',
        state: 'active',
        locale: 'en-US',
        voice: 'en-US-AvaNeural',
        consent_version: 'consent-v3-memory',
        transports_allowed: { webrtc: true, relay: false }
      })
    };
  });
  const local = withFocus(() => null);
  local.setCapability({ ...cap, validation_metrics: true });
  await local.start();
  for (let i = 0; i < 5; i++) await flush();
  void local.submitTranscript(transcript('item-1', 'first'));
  await flush();
  void local.submitTranscript(transcript('item-2', 'second'));
  await flush();
  // The queue wait is visible before any server timer exists.
  expect(state.getState().queued).toBe(1);
  held = false;
  releaseTurn(turnOk);
  for (let i = 0; i < 10; i++) await flush();
  const turns = requests.filter((r) => r.path.endsWith('/turns'));
  expect(turns).toHaveLength(2);
  for (const turn of turns) {
    // Enqueue→submit queue wait on the browser monotonic clock; nothing else
    // in the transcript/idempotency payload changes shape.
    const timing = turn.body?.timing as { ms_queue?: unknown } | undefined;
    expect(typeof timing?.ms_queue).toBe('number');
    expect(timing?.ms_queue as number).toBeGreaterThanOrEqual(0);
    expect(timing?.ms_queue as number).toBeLessThanOrEqual(300_000);
    expect(turn.body?.item_id).toMatch(/^item-/);
    expect(turn.body?.transcript).toBeTruthy();
  }
  await local.end();
});

it('Off keeps required outcomes: turns and help still run', async () => {
  prefs = { ...prefs, voiceProgressMode: 'off' };
  const local = withFocus(() => null);
  await local.start();
  await local.submitTranscript(transcript('item-1'));
  for (let i = 0; i < 5; i++) await flush();
  const turn = requests.find((r) => r.path.endsWith('/turns'));
  expect(turn?.body?.progress_mode).toBe('off');
  expect(state.getState().lastSubmitted?.status).toBe('complete');
  await local.help();
  expect(
    requests.some((r) => r.path.includes('help') || r.path.endsWith('/prompts'))
  ).toBe(true);
  await local.end();
});

// --- VOICE-H08: warm-up finals must survive the pre-connect window -------

/** Deliver a completed final transcript through the real event handler. */
const emitFinal = (
  itemId: string,
  text: string,
  target: VoiceSessionController = controller
) =>
  target.handleEvent(
    JSON.stringify({
      type: 'conversation.item.input_audio_transcription.completed',
      transcript: text,
      item_id: itemId
    })
  );
const turnRequests = () => requests.filter((r) => r.path.endsWith('/turns'));
const dispatchRequests = () =>
  requests.filter((r) =>
    /\/(turns|prompts|cancel|suspend|decision|help|presentation|timing)/.test(
      r.path
    )
  );
/** Gate the SDP answer so the session is live but the transport is not connected. */
const gateSdp = () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const base = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (url: string, options: RequestInit) => {
    if (new URL(url).pathname.endsWith('/sdp')) await gate;
    return base(url, options);
  });
  return release;
};
const flush8 = async () => {
  for (let i = 0; i < 8; i++) await flush();
};

it('VOICE-H08: holds a warm-up final visibly with zero dispatch until explicit connected confirmation', async () => {
  const releaseSdp = gateSdp();
  const heard: string[] = [];
  controller.listeners.add({
    onFinalTranscript: (value) => heard.push(value.itemId)
  });
  const started = controller.start();
  await flush8();
  expect(state.getState().transport).toBe('connecting');
  expect(state.getState().session).not.toBeNull();
  emitFinal('warm-1', 'close the purchase order for machine four');
  const held = state.getState().pendingConfirm;
  expect(held?.itemId).toBe('warm-1');
  expect(held?.text).toBe('close the purchase order for machine four');
  expect(state.getState().notice).toBe('warmup_held');
  expect(heard).toContain('warm-1');
  // No review prompt, provider dispatch or business submit while disconnected.
  expect(dispatchRequests()).toEqual([]);
  // A pre-connect "yes" is never an approved action.
  emitFinal('warm-yes', 'yes');
  expect(state.getState().pendingConfirm?.itemId).toBe('warm-1');
  await controller.confirmPending();
  expect(state.getState().pendingConfirm?.itemId).toBe('warm-1');
  expect(dispatchRequests()).toEqual([]);
  releaseSdp();
  await started;
  expect(state.getState().transport).toBe('connected');
  // Connecting never auto-executes or replays the held warm-up.
  expect(state.getState().pendingConfirm?.itemId).toBe('warm-1');
  expect(dispatchRequests()).toEqual([]);
  // Explicit confirmation post-connect sends exactly once.
  await controller.confirmPending();
  await flush();
  const warmTurns = () =>
    turnRequests().filter((r) => r.body?.item_id === 'warm-1');
  expect(warmTurns()).toHaveLength(1);
  expect(warmTurns()[0].body?.transcript).toBe(
    'close the purchase order for machine four'
  );
  // Explicit confirmation and repeated spoken confirm never resend it.
  await controller.confirmPending();
  emitFinal('warm-yes-2', 'yes');
  await flush();
  expect(warmTurns()).toHaveLength(1);
  // Fresh connected capture still auto-submits (positive control).
  emitFinal('fresh-1', 'what is its status?');
  await flush();
  expect(
    turnRequests().filter((r) => r.body?.item_id === 'fresh-1')
  ).toHaveLength(1);
});

it('VOICE-H08: double warm-up finals revise the hold; a drop discards it and reconnect capture never auto-fires', async () => {
  const releaseSdp = gateSdp();
  const started = controller.start();
  await flush8();
  emitFinal('warm-a', 'first warm-up words');
  emitFinal('warm-b', 'second warm-up words');
  const held = state.getState().pendingConfirm;
  expect(held?.itemId).toBe('warm-b');
  expect(held?.revision).toBe(2);
  expect(held?.supersedes).toBe('warm-a');
  expect(state.getState().revisions.map((r) => r.itemId)).toEqual([
    'warm-a',
    'warm-b'
  ]);
  expect(dispatchRequests()).toEqual([]);
  releaseSdp();
  await started;
  expect(state.getState().pendingConfirm?.itemId).toBe('warm-b');
  expect(dispatchRequests()).toEqual([]);
  // Connection drop mutes: the mute boundary discards the warm-up safely.
  FakePeer.peers[0].channel.dispatchEvent(new Event('close'));
  await flush();
  expect(state.getState().transport).toBe('reconnecting');
  expect(state.getState().pendingConfirm).toBeNull();
  // A final during reconnect (muted, not connected) is neither held nor sent.
  emitFinal('warm-c', 'third warm-up words');
  emitFinal('warm-c-yes', 'yes');
  await flush();
  expect(state.getState().pendingConfirm).toBeNull();
  expect(turnRequests()).toEqual([]);
  await controller.confirmPending();
  expect(turnRequests()).toEqual([]);
});

it('VOICE-H08: hidden/focus/logout/end boundaries and changed fences discard the warm-up without submitting', async () => {
  // Hidden boundary.
  let releaseSdp = gateSdp();
  let started = controller.start();
  await flush8();
  emitFinal('warm-h', 'hidden boundary words');
  releaseSdp();
  await started;
  doc.hidden = true;
  doc.dispatchEvent(new Event('visibilitychange'));
  expect(state.getState().pendingConfirm).toBeNull();
  await controller.confirmPending();
  expect(turnRequests()).toEqual([]);
  doc.hidden = false;
  await controller.end();

  // Focus boundary discards the hold before any confirmation can send.
  let stamp: number | null = 7;
  const local = withFocus(() => stamp);
  releaseSdp = gateSdp();
  started = local.start();
  await flush8();
  emitFinal('warm-f', 'focus boundary words', local);
  local.focusChanged();
  expect(state.getState().pendingConfirm).toBeNull();
  releaseSdp();
  await started;
  await local.confirmPending();
  expect(turnRequests()).toEqual([]);
  await local.end();

  // Confirm-time fence: a changed focus stamp discards instead of sending.
  stamp = 7;
  releaseSdp = gateSdp();
  started = local.start();
  await flush8();
  emitFinal('warm-f2', 'stale fence words', local);
  releaseSdp();
  await started;
  stamp = 8;
  await local.confirmPending();
  expect(turnRequests()).toEqual([]);
  expect(state.getState().pendingConfirm).toBeNull();
  expect(state.getState().notice).toBe('warmup_discarded');
  await local.end();

  // Logout and thread-switch (account/session) boundaries.
  releaseSdp = gateSdp();
  started = controller.start();
  await flush8();
  emitFinal('warm-l', 'logout boundary words');
  releaseSdp();
  await started;
  controller.logout();
  expect(state.getState().pendingConfirm).toBeNull();
  await controller.confirmPending();
  expect(turnRequests()).toEqual([]);

  releaseSdp = gateSdp();
  started = controller.start();
  await flush8();
  emitFinal('warm-t', 'thread switch words');
  releaseSdp();
  await started;
  controller.configure('https://app.test/api/ai', 'thread-2');
  await flush();
  expect(state.getState().pendingConfirm).toBeNull();
  await controller.confirmPending();
  expect(turnRequests()).toEqual([]);
});
