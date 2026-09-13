import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { type StoreApi, createStore } from 'zustand/vanilla';
import type { VoiceDecisionStore } from '../../../states/VoiceDecisionState';
import { emptyDecisionState } from '../../../states/decisionReducer';
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
  consent_version: 'consent-v2',
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
      consent_version: 'consent-v2',
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
    voiceConsentVersion: 'consent-v2',
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
