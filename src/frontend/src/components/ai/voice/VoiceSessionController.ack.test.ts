import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { type StoreApi, createStore } from 'zustand/vanilla';
import type { VoiceDecisionStore } from '../../../states/VoiceDecisionState';
import { emptyDecisionState } from '../../../states/decisionReducer';
import {
  type ContextPreflight,
  type ContextPreflightOutcome,
  runContextPreflight
} from '../../aichat/contextPreflight';
import { VoiceSessionController } from './VoiceSessionController';
import {
  type VoiceCapability,
  type VoicePreferences,
  type VoiceSnapshot,
  initialVoiceSnapshot
} from './types';

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
import { useThreadFocusState } from '../../../states/ThreadFocusState';
import type { ThreadFocusPayload } from '../../aichat/conversationFocus';
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
    voiceReviewEveryRequest: false,
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

// Native controller + actual shared preflight + actual focus store. Only
// HTTP/WebRTC/media browser APIs use the established disposable test fixtures.
const focus91 = () => useThreadFocusState.getState();
const own91: ThreadFocusPayload = {
  focus: {
    kind: 'work_order',
    id: 42,
    label: 'WO-0042 exact old ACK',
    machine_id: 17,
    work_order_id: 42
  },
  focus_version: 1,
  focus_unavailable: false
};
const newer91: ThreadFocusPayload = {
  focus: {
    kind: 'kanban_card',
    id: 77,
    label: 'Task 77 newer visible focus',
    machine_id: 17,
    work_order_id: 42
  },
  focus_version: 2,
  focus_unavailable: false
};

function actualPreflight91(held: boolean) {
  focus91().unbind();
  focus91().bind({
    threadId: 'thread-1',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  focus91().applyAck({
    focus: null,
    focus_version: 0,
    focus_unavailable: false
  });
  let releaseAck = () => {};
  const barrier = new Promise<void>((resolve) => {
    releaseAck = resolve;
  });
  const seen: ContextPreflightOutcome[] = [];
  const originalFetch = fetcher;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options: RequestInit) => {
      const path = new URL(url).pathname;
      const method = options.method ?? 'GET';
      const body = options.body ? JSON.parse(String(options.body)) : null;
      if (path.endsWith('/focus') && method === 'PUT') {
        requests.push({ path, method, body });
        if (held) await barrier;
        return { ok: true, status: 200, json: async () => own91 };
      }
      // Model the existing server version fence, not a permissive fake mint.
      if (
        path.endsWith('/sessions') &&
        method === 'POST' &&
        body?.expected_focus_version !== focus91().version
      ) {
        requests.push({ path, method, body });
        return {
          ok: false,
          status: 409,
          json: async () => ({ detail: 'VOICE_FOCUS_CHANGED' })
        };
      }
      return originalFetch(url, options);
    })
  );
  const preflight: ContextPreflight = async (entry) => {
    expect(entry).toEqual({ threadId: 'thread-1', entry: 'voice' });
    const outcome = await runContextPreflight({
      hint: { kind: 'work_order', id: 42, label: 'WO-0042 exact old ACK' },
      scopeCapable: true,
      focusCapable: true,
      threadShared: false,
      confirmed: null,
      threadId: entry.threadId,
      applyScope: async () => {
        throw new Error('WorkOrder must not widen scope');
      },
      applyFocus: async (ref) => {
        let version: number | undefined;
        const result = await focus91().select(ref, (acknowledged) => {
          version = acknowledged;
        });
        return { ...result, version };
      },
      contextUnchanged: () => focus91().binding?.threadId === entry.threadId
    });
    seen.push(outcome);
    return outcome;
  };
  controller = new VoiceSessionController(
    state,
    decisions,
    () => prefs,
    (values) => {
      prefs = { ...prefs, ...values };
    },
    preflight,
    () => focus91().version
  );
  controller.configure('https://app.test/api/ai', 'thread-1');
  controller.setCapability(cap);
  return { releaseAck, seen };
}

afterEach(() => focus91().unbind());

it('RV09 voice ordered own ACK1 reaches a fresh simulated session with stamp1', async () => {
  const probe = actualPreflight91(false);
  await controller.start();
  expect(probe.seen).toEqual([
    { ok: true, threadId: 'thread-1', focusVersion: 1 }
  ]);
  const creates = requests.filter(
    (r) => r.method === 'POST' && r.path.endsWith('/sessions')
  );
  expect(creates).toHaveLength(1);
  expect(creates[0].body?.expected_focus_version).toBe(1);
  expect(state.getState().session?.id).toBe('session-1');
  expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1); // FAKE only.
  expect(FakePeer.peers).toHaveLength(1);
  expect(FakePeer.peers[0].setRemoteDescription).toHaveBeenCalledTimes(1);
});

it('RV09 voice held old ACK1 cannot mint with newer unrelated global stamp2', async () => {
  const probe = actualPreflight91(true);
  const pending = controller.start();
  try {
    await flush();
    expect(
      requests.filter((r) => r.method === 'PUT' && r.path.endsWith('/focus'))
    ).toHaveLength(1);
    expect(probe.seen).toEqual([]);
    // A legitimate newer server ACK is observed on the SAME binding while
    // the original PUT response remains held. No thread/account switch.
    focus91().applyAck(newer91, 'thread-1');
    expect(focus91().version).toBe(2);
    probe.releaseAck();
    await pending;
    expect(probe.seen).toEqual([
      { ok: true, threadId: 'thread-1', focusVersion: 1 }
    ]);
    expect(focus91().version).toBe(2);
    expect(focus91().focus).toEqual(newer91.focus);
    const creates = requests.filter(
      (r) => r.method === 'POST' && r.path.endsWith('/sessions')
    );
    console.log(
      'RV09_NATIVE91',
      JSON.stringify({
        preflight: probe.seen,
        globalVersion: focus91().version,
        sessionPosts: creates.map((r) => r.body),
        adoptedSession: state.getState().session?.id ?? null,
        fakeMediaCalls: vi.mocked(navigator.mediaDevices.getUserMedia).mock
          .calls.length
      })
    );
    // Either refuse locally or stamp the original ACK and let the real
    // version-fence contract refuse. Never silently adopt newer focus2.
    expect(creates.map((r) => r.body?.expected_focus_version)).not.toContain(2);
    expect(state.getState().session).toBeNull();
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  } finally {
    probe.releaseAck();
    await pending;
  }
});
