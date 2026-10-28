/** Foreground transport owner. React surfaces subscribe; they never own media. */
import type { StoreApi } from 'zustand/vanilla';
import type {
  VoiceFinalTranscript,
  VoiceSessionPayload,
  VoiceSpokenPayload,
  VoiceTurnResponse
} from '../../../../lib/types/Voice';
import type { VoiceDecisionStore } from '../../../states/VoiceDecisionState';
import {
  type DecisionSnapshot,
  decisionContext
} from '../../../states/decisionReducer';
import type {
  ContextPreflight,
  ContextPreflightOutcome
} from '../../aichat/contextPreflight';
import { VOICE_FOCUS_CHANGED } from '../../aichat/conversationFocus';
import { DecisionPlayback } from '../decisionPlayback';
import {
  VOICE_CONFIRM_RE,
  VOICE_DISCARD_RE,
  VOICE_STOP_RE,
  isBareDecisionUtterance,
  isVoiceTargetCorrection,
  normalizeDecisionUtterance,
  shouldHoldTranscript
} from '../voiceCriticalTerms';
import { monitorMicrophone, outputDisappeared } from './audioRoute';
import { type Earcon, Earcons } from './earcons';
import { selectedCandidateDiagnostics } from './networkDiagnostics';
import { TurnQueue } from './turnQueue';
import {
  type VoiceCapability,
  type VoiceNotice,
  type VoicePreferences,
  type VoiceSnapshot,
  legacyClientState
} from './types';
import { VoiceHttpError, endOnPageHide, voiceHttp } from './voiceHttp';
import { ForegroundBounds, installVoiceLifecycle } from './voiceLifecycle';
import { VoiceLifecycleRelay } from './voiceLifecycleRelay';
import { VoiceTiming } from './voiceTiming';

const TERMINAL = ['ended', 'expired', 'failed'];
const FILLER = /^(?:uh|um|hmm+|mm+|mhm|huh|erm|ah|oh)[.!?,\s]*$/i;
const TRANSCRIPT_CORRECTION =
  /^(?:correction[:,]?|replace (?:the )?transcript[:,]?)\s+\S/i;
export class VoiceSessionController {
  private host = '';
  private threadId: string | undefined;
  /** Thread requested when the live session was created (null = unrequested). */
  private sessionRequestThread: string | null = null;
  private peer: RTCPeerConnection | null = null;
  private channel: RTCDataChannel | null = null;
  private stream: MediaStream | null = null;
  private audio: HTMLAudioElement | null = null;
  private generation = 0;
  private starting = false;
  private permissionPending = false;
  private submitting = false;
  private lifecycleRelay = new VoiceLifecycleRelay();
  private held = false;
  private seen = new Set<string>();
  private queue = new TurnQueue<VoiceFinalTranscript>(3, () => this.dropped());
  private bounds = new ForegroundBounds(300_000);
  private playback = new DecisionPlayback();
  private reporting = false;
  private lifecycleCleanup: (() => void) | null = null;
  private microphoneCleanup: (() => void) | null = null;
  private connectTimer: number | undefined;
  private ttsTimer: number | undefined;
  private reconnectTimer: number | undefined;
  private reconnectAttempts = 0;
  private devices: MediaDeviceInfo[] = [];
  private wakeLock: WakeLockSentinel | null = null;
  private ears = new Earcons();
  private outputEpoch = 0;
  private suspension: Promise<unknown> = Promise.resolve();
  private abort = new AbortController();
  private droppedPending = false;
  /**
   * VOICE-H08: a warm-up final held for VISIBLE explicit review while the
   * transport was not yet connected (null = none). It is never executed or
   * replayed automatically on connection; see the confirm-time fences.
   */
  private warmupHold: VoiceFinalTranscript | null = null;
  /** Account/thread/focus/decision/session captured when the warm-up was held. */
  private warmupFence: {
    sessionId: string | null;
    threadId: string | null;
    focusVersion: number | null;
    operationId: string | null;
    utteranceId: string | null;
  } | null = null;
  /**
   * Plan C: focus stamps captured at ENQUEUE (itemId → `focus_version` the
   * user saw when the words were captured; null = never observed). Queued
   * words keep their capture stamp and are never rebound to a later focus.
   */
  private focusStamps = new Map<string, number | null>();
  /**
   * Ephemeral per-transcript enqueue clocks (the same browser monotonic
   * clock as the timing report) for the visible client queue wait
   * (`ms_queue`). Bounded by the queue itself and cleared on every
   * queue/focus/thread/end/hidden boundary so no cross-generation wait can
   * ever be reported or rebound. Never touches transcripts, idempotency,
   * capture-time focus stamps or approval state.
   */
  private enqueueClocks = new Map<string, number>();
  /**
   * Plan C: invalidates result callbacks/timing across a confirmed focus
   * change WITHOUT touching the session generation (the live data-channel
   * handlers are fenced by the session generation and must keep working).
   */
  private resultEpoch = 0;
  private idleEnding = false;
  private outputEnergy = 0;
  private timingRtpSource: string | null = null;
  private timing = new VoiceTiming(
    (report) => {
      if (!this.snapshot.capability?.validation_metrics || this.snapshot.hidden)
        return;
      // Numeric/opaque-ID payload only; harness collection does not require audio.
      window.dispatchEvent(
        new CustomEvent('aimms:voice-timing', { detail: report })
      );
      void this.control('timing', report).catch(() => {});
    },
    undefined,
    undefined,
    (reason) => {
      if (this.snapshot.capability?.validation_metrics && !this.snapshot.hidden)
        window.dispatchEvent(
          new CustomEvent('aimms:voice-timing-diagnostic', {
            detail: { reason }
          })
        );
    }
  );
  private timingGeneration = 0;
  private refreshTiming() {
    const generation = ++this.timingGeneration;
    this.timing.reset();
    if (!this.snapshot.capability?.validation_metrics) return;
    void this.control<{ epoch: string }>('timing-epoch')
      .then((body) => {
        if (
          body &&
          generation === this.timingGeneration &&
          !this.snapshot.hidden
        )
          this.timing.reset(body.epoch);
      })
      .catch(() => {});
  }
  private lastAudioAt = 0;
  readonly listeners = new Set<{
    onTurnResult?: (turn: VoiceTurnResponse) => void;
    onFinalTranscript?: (value: VoiceFinalTranscript) => void;
    /**
     * Plan C: a turn/session refused with a stale focus stamp
     * (`VOICE_FOCUS_CHANGED`). The session stays alive; the UI re-reads the
     * authoritative thread focus. Never a retry signal.
     */
    onFocusStale?: () => void;
  }>();
  constructor(
    private store: StoreApi<VoiceSnapshot>,
    private decisions: Pick<StoreApi<VoiceDecisionStore>, 'getState'>,
    private preferences: () => VoicePreferences,
    private savePreferences: (values: Partial<VoicePreferences>) => void,
    /**
     * Shared machine-context preflight (plan 3.1): awaited before any entry
     * path mints a session (microphone, consent completion, keyboard start
     * and restart all funnel through `start`). A failed or stale outcome
     * blocks the start; its thread id is authoritative for session creation.
     */
    private contextPreflight?: ContextPreflight,
    /**
     * Plan C: the CURRENT thread's server-confirmed `focus_version` ("the
     * version the user saw"), captured at enqueue and stamped on every turn.
     * Null = never observed: the stamp is omitted entirely.
     */
    private focusStamp?: () => number | null
  ) {}
  get snapshot() {
    return this.store.getState();
  }
  private update(change: Partial<VoiceSnapshot>) {
    const next = { ...this.snapshot, ...change };
    this.store.setState({ ...next, state: legacyClientState(next) });
  }
  configure(host: string, threadId?: string) {
    if (this.host && this.host !== host) void this.end();
    this.host = host;
    const active = this.snapshot.session;
    if (!active) {
      this.threadId = threadId;
      return;
    }
    if (
      threadId === undefined ||
      threadId === this.threadId ||
      threadId === this.sessionRequestThread
    ) {
      // No visible thread, the session's server-assigned thread arriving
      // back, or the thread the session was created for (the initial
      // undefined→server-created adoption window): never an actual switch.
      return;
    }
    // Actual thread switch during a live session: end the old session before
    // any rebind — disable submits, invalidate generation/output, clear the
    // queue, abort stale requests and DELETE the server session. Nothing is
    // replayed into the new thread (plan 3.1.4).
    this.threadId = threadId;
    void this.end();
  }
  setCapability(capability: VoiceCapability | null) {
    if (!capability?.validation_metrics) {
      this.timingGeneration++;
      this.timing.reset();
    }
    this.update({
      capability,
      persistence: capability?.foreground_session
        ? 'keep_on_minimize'
        : 'end_on_close'
    });
    if (
      (!capability?.enabled || capability.runtime?.available === false) &&
      this.snapshot.session
    )
      void this.end();
  }
  private request<T>(path: string, method = 'GET', body?: unknown) {
    return voiceHttp<T>(this.host, path, method, body, this.abort.signal).catch(
      async (error) => {
        if (
          error instanceof VoiceHttpError &&
          error.code === 'VOICE_SESSION_OWNER_MISMATCH'
        ) {
          await this.end('voice_restart_required');
        }
        throw error;
      }
    );
  }
  private async control<T>(path: string, body?: unknown) {
    const session = this.snapshot.session;
    if (!session) return null;
    return this.request<T>(`sessions/${session.id}/${path}`, 'POST', body);
  }
  private cue(kind: Earcon) {
    const s = this.snapshot;
    const p = this.preferences();
    return this.ears.play(kind, {
      enabled: p.voiceEarcons,
      muted: s.muted,
      hidden: s.hidden,
      playing: s.playback === 'playing',
      sr: p.voiceSrAnnounceTranscripts
    });
  }
  private gateMicrophone() {
    const s = this.snapshot;
    const active = Boolean(
      s.session &&
        s.transport === 'connected' &&
        !s.hidden &&
        !s.muted &&
        (s.mode === 'continuous' || this.held)
    );
    this.stream?.getAudioTracks().forEach((track) => {
      track.enabled = active;
    });
    this.update({
      mic: !s.session
        ? 'off'
        : s.hidden || s.transport !== 'connected'
          ? 'suspended'
          : s.muted
            ? 'muted'
            : active
              ? 'listening'
              : 'ptt_idle'
    });
  }
  setMode = (mode: VoicePreferences['voiceListeningMode']) => {
    this.held = false;
    this.savePreferences({ voiceListeningMode: mode });
    this.update({ mode });
    this.gateMicrophone();
  };
  pushToTalk = (down: boolean) => {
    this.held = down && !document.hidden;
    this.gateMicrophone();
    if (down) this.bounds.touch();
  };
  toggleMute = () => {
    if (this.snapshot.transport !== 'connected') return;
    this.update({ muted: !this.snapshot.muted });
    this.gateMicrophone();
    this.discardWarmup('warmup_discarded');
  };
  private installLifecycle() {
    this.lifecycleCleanup?.();
    const remove = installVoiceLifecycle({
      hide: () => this.hide(),
      show: () => void this.show(),
      pagehide: () => {
        const active = this.snapshot.session;
        if (active) endOnPageHide(this.host, active.id);
        this.releaseMedia();
        void this.end();
      },
      offline: () => this.connectionLost(),
      online: () => this.scheduleReconnect(),
      tick: () => {
        if (!this.snapshot.session) return;
        if (this.bounds.expired) {
          void this.end(this.snapshot.hidden ? 'ended_away' : 'idle_ended');
          return;
        }
        if (
          !this.snapshot.hidden &&
          this.bounds.remainingMs <= 2000 &&
          !this.idleEnding
        ) {
          this.idleEnding = true;
          // Request the fixed phrase before server expiry; never extend the bound.
          void this.prompt('status', 'session_ended');
        } else if (this.bounds.remainingMs > 2000) this.idleEnding = false;
        void this.reportPlayback();
        void this.checkOutput();
      }
    });
    navigator.mediaDevices?.addEventListener('devicechange', this.deviceChange);
    window.addEventListener('blur', this.releasePushToTalk);
    this.lifecycleCleanup = () => {
      remove();
      navigator.mediaDevices?.removeEventListener(
        'devicechange',
        this.deviceChange
      );
      window.removeEventListener('blur', this.releasePushToTalk);
    };
  }
  private releasePushToTalk = () => this.pushToTalk(false);
  private async acquireWakeLock() {
    if (
      !this.snapshot.capability?.wake_lock ||
      document.hidden ||
      !this.snapshot.session ||
      !navigator.wakeLock
    )
      return;
    try {
      const lock = await navigator.wakeLock.request('screen');
      if (document.hidden || !this.snapshot.session) {
        await lock.release();
        return;
      }
      this.wakeLock = lock;
    } catch {
      /* Unsupported/denied wake lock never prevents foreground use. */
    }
  }
  private releaseWakeLock() {
    void this.wakeLock?.release().catch(() => {});
    this.wakeLock = null;
  }
  private hide() {
    this.timingGeneration++;
    // Hidden boundary: a missing onset becomes hidden-page coverage, never a
    // zero measurement.
    this.timing.finish('hidden_page');
    this.timing.reset();
    if (this.starting && this.snapshot.transport !== 'connected') {
      void this.end('ended_away');
      return;
    }
    this.bounds.hide();
    this.held = false;
    this.discardWarmup();
    this.update({ hidden: true, notice: 'hidden' });
    this.gateMicrophone();
    this.stopLocal();
    this.releaseWakeLock();
    this.queue.clear();
    this.update({ queued: 0 });
    // Set-aside does not require a possibly stale client decision snapshot.
    this.suspension = this.control('suspend').catch(() => null);
    this.enqueueClocks.clear();
  }
  private async show() {
    if (!this.snapshot.session) return;
    const generation = this.generation;
    if (this.bounds.show()) {
      await this.end('ended_away');
      return;
    }
    this.update({ hidden: false });
    try {
      await this.suspension;
      if (generation !== this.generation || document.hidden) return;
      await this.control('suspend'); // Retry a hidden/offline failure before enabling capture.
      const active = this.snapshot.session;
      if (!active) return;
      const session = await this.request<VoiceSessionPayload>(
        `sessions/${active.id}`
      );
      if (generation !== this.generation || document.hidden) return;
      if (TERMINAL.includes(session.state)) {
        await this.end('ended_away');
        return;
      }
      await this.decisions.getState().refresh();
      if (generation !== this.generation || document.hidden) return;
      this.update({ notice: 'readback_available' });
      this.refreshTiming();
      this.gateMicrophone();
      await this.acquireWakeLock();
      if (this.snapshot.transport === 'reconnecting') this.scheduleReconnect();
    } catch {
      if (generation === this.generation) await this.end('ended_away');
    }
  }
  private deviceChange = async () => {
    if (!this.snapshot.session) return;
    const generation = this.generation;
    try {
      const next = await navigator.mediaDevices.enumerateDevices();
      if (generation !== this.generation) return;
      const lost = outputDisappeared(this.devices, next);
      this.devices = next;
      this.updateOutputs();
      this.update({ notice: 'route_changed' });
      if (lost && this.snapshot.capability?.route_loss_pause) {
        await this.cancel();
        if (generation !== this.generation) return;
        // Only the fixed route notice may play on a replacement system route.
        // Resuming the interrupted answer still requires an explicit request.
        await this.prompt('status', 'route_changed');
      }
    } catch {
      /* Do not claim a route was lost when enumeration is unsupported. */
    }
  };
  private updateOutputs() {
    this.update({
      outputs: this.devices
        .filter((device) => device.kind === 'audiooutput')
        .map((device) => ({
          value: device.deviceId,
          label: device.label || device.deviceId
        })),
      outputSelectionSupported: Boolean(this.audio && 'setSinkId' in this.audio)
    });
  }
  selectOutput = async (deviceId: string) => {
    if (
      !this.audio ||
      !('setSinkId' in this.audio) ||
      (deviceId !== '' &&
        !this.devices.some(
          (device) =>
            device.kind === 'audiooutput' && device.deviceId === deviceId
        ))
    )
      return;
    await this.cancel();
    try {
      await this.audio.setSinkId(deviceId);
      this.update({ outputDevice: deviceId, notice: 'route_changed' });
    } catch {
      this.update({ notice: 'audio_blocked' });
    }
  };
  private async playAudio() {
    if (this.snapshot.hidden || !this.audio) return;
    const audio = this.audio;
    try {
      await audio.play();
    } catch {
      if (audio !== this.audio || this.snapshot.hidden) return;
      this.update({ playback: 'blocked', notice: 'audio_blocked' });
      navigator.vibrate?.(120);
    }
  }
  private async checkOutput() {
    if (!this.peer || this.snapshot.hidden || this.audio?.paused) return;
    const peer = this.peer;
    const timingContext = this.timing.samplingContext;
    try {
      const stats = await peer.getStats();
      if (peer !== this.peer || this.snapshot.hidden || this.audio?.paused)
        return;
      let energy = 0;
      const measured: { id: string; total: number }[] = [];
      stats.forEach((report) => {
        if (report.type === 'inbound-rtp' && report.kind === 'audio') {
          energy += Number(report.totalAudioEnergy ?? 0);
          if (
            typeof report.id === 'string' &&
            typeof report.totalAudioEnergy === 'number' &&
            Number.isFinite(report.totalAudioEnergy) &&
            report.totalAudioEnergy >= 0
          )
            measured.push({ id: report.id, total: report.totalAudioEnergy });
        }
      });
      if (timingContext !== this.timing.samplingContext) return;
      if (
        measured.length === 1 &&
        this.audio &&
        !this.audio.muted &&
        this.audio.volume !== 0
      ) {
        if (
          this.timingRtpSource !== null &&
          this.timingRtpSource !== measured[0].id
        )
          this.timing.invalidateEnergy();
        this.timingRtpSource = measured[0].id;
        this.timing.sampleEnergy(measured[0].total, timingContext);
      } else this.timing.invalidateEnergy();
      if (energy > this.outputEnergy) {
        this.lastAudioAt = Date.now();
        if (['pending', 'playing'].includes(this.snapshot.playback))
          this.update({ playback: 'playing' });
        if (!this.idleEnding) this.bounds.touch();
      } else if (
        this.snapshot.playback === 'playing' &&
        Date.now() - this.lastAudioAt > 1500 &&
        this.decisions.getState().decision?.state !== 'presented'
      ) {
        this.update({ playback: 'idle' });
      }
      this.outputEnergy = energy;
    } catch {
      /* Some transports lack energy stats; pending timeout remains honest. */
    }
  }
  private expectSpeech(spoken: VoiceSpokenPayload | null) {
    if (spoken?.channel_closed === true) {
      void this.end('voice_restart_required');
      return;
    }
    this.timing.bind(spoken);
    this.update({ lastSpoken: spoken });
    window.clearTimeout(this.ttsTimer);
    if (spoken?.playback_state !== 'requested') {
      this.update({ playback: spoken ? 'text_only' : 'idle' });
      return;
    }
    // Ordinary speech can finish on the provider channel before the HTTP
    // result arrives. Preserve that observed UI stop instead of reopening
    // pending playback. This never marks a decision as heard or confirmed.
    if (
      this.decisions.getState().decision?.state !== 'presented' &&
      this.playback.ordinaryOutputStopped(spoken)
    ) {
      this.update({ playback: 'idle' });
      return;
    }
    this.update({ playback: 'pending' });
    void this.playAudio();
    this.ttsTimer = window.setTimeout(
      () => {
        if (this.snapshot.playback === 'pending') {
          this.playback.stop();
          this.update({ playback: 'text_only', notice: 'tts_timeout' });
          void this.control('cancel').catch(() => {});
        }
      },
      (this.snapshot.capability?.tts_timeout_s ?? 8) * 1000
    );
  }
  private stopLocal() {
    this.outputEpoch++;
    this.playback.stop();
    // Output boundary: honest coverage for a missing onset.
    this.timing.finish(this.snapshot.hidden ? 'hidden_page' : undefined);
    this.audio?.pause();
    window.clearTimeout(this.ttsTimer);
    this.update({ playback: 'paused' });
  }
  private outputIsCurrent(generation: number, epoch: number) {
    if (generation !== this.generation) return false;
    if (epoch !== this.outputEpoch || this.snapshot.hidden) {
      // A provider may have accepted a request while Stop/Hide was in flight.
      void this.control('cancel').catch(() => {});
      return false;
    }
    return true;
  }
  cancel = () => this.cancelOutput(true);
  private async cancelOutput(measureLocal: boolean) {
    const stopStarted = performance.now();
    this.audio?.pause();
    if (measureLocal) this.timing.localStop(stopStarted);
    this.stopLocal(); // Always before any network/queue wait.
    this.bounds.touch();
    try {
      await this.control('cancel');
    } catch {
      /* Local stop is already complete. */
    }
  }
  async prompt(kind: 'help' | 'status', status?: string) {
    if (this.snapshot.hidden || !this.snapshot.session) return;
    const generation = this.generation;
    this.stopLocal();
    const epoch = this.outputEpoch;
    try {
      const spoken = await this.control<VoiceSpokenPayload>('prompts', {
        kind,
        status
      });
      if (this.outputIsCurrent(generation, epoch)) this.expectSpeech(spoken);
    } catch {
      /* Visible status remains; do not invent spoken delivery. */
    }
  }
  help = () => {
    this.bounds.touch();
    if (this.decisions.getState().decision?.state === 'presented')
      return this.resumeOutput();
    return this.prompt('help');
  };
  walkthrough = async (
    workOrderId: number,
    position: number,
    utterance: string,
    value?: string,
    passed?: boolean
  ) => {
    const active = this.snapshot.session;
    if (
      !active ||
      this.snapshot.hidden ||
      this.snapshot.transport !== 'connected'
    )
      return null;
    this.bounds.touch();
    this.stopLocal();
    const generation = this.generation;
    const outputEpoch = this.outputEpoch;
    const epoch = this.playback.beginTurn();
    this.timing.begin();
    const reply = await this.request<
      DecisionSnapshot & {
        position: number;
        total: number;
        speak_text: string;
        spoken: VoiceSpokenPayload;
      }
    >('procedures/walkthrough', 'POST', {
      work_order_id: workOrderId,
      position,
      utterance,
      value,
      passed,
      session_id: active.id
    });
    if (!this.outputIsCurrent(generation, outputEpoch)) return null;
    this.decisions.getState().applyTurn(active.id, reply);
    this.playback.bindTurn(epoch, reply.pending_decision);
    this.expectSpeech(reply.spoken);
    return reply;
  };
  resumeOutput = async () => {
    if (this.snapshot.hidden) return;
    this.bounds.touch();
    this.ears.unlock();
    const decision = this.decisions.getState().decision;
    if (decision?.state === 'presented' || decision?.operation_id) {
      await this.readDecision(
        decision.state === 'presented' ? 'readback' : 'repeat'
      );
    } else if (this.snapshot.pendingConfirm) {
      await this.requestHoldPrompt(this.snapshot.pendingConfirm);
    } else if (this.snapshot.presentation) {
      await this.present('repeat');
    } else {
      // No business turn retry. A suspended decision needs a fresh preview.
      this.update({ notice: 'readback_available' });
    }
  };
  private async readDecision(action: 'readback' | 'repeat') {
    const generation = this.generation;
    this.stopLocal();
    const outputEpoch = this.outputEpoch;
    const epoch = this.playback.beginTurn();
    this.timing.begin();
    if (this.cue('decision'))
      await new Promise<void>((resolve) => window.setTimeout(resolve, 125));
    if (!this.outputIsCurrent(generation, outputEpoch)) return;
    try {
      await this.decisions.getState().decide(action);
      if (!this.outputIsCurrent(generation, outputEpoch)) return;
      const decision = this.decisions.getState().decision;
      this.playback.bindTurn(epoch, decision);
      if (decision?.utterance_id)
        this.expectSpeech({
          utterance_id: decision.utterance_id,
          spoken_summary: decision.spoken_summary,
          spoken_summary_hash: decision.spoken_summary_hash,
          playback_state: decision.delivery_state
        });
      else await this.playAudio();
    } catch {
      if (this.outputIsCurrent(generation, outputEpoch))
        this.update({ notice: 'readback_available' });
    }
  }
  present = async (action: 'next' | 'repeat' | 'slower' | 'short') => {
    const presentation = this.snapshot.presentation;
    if (!presentation || this.snapshot.hidden) return;
    this.bounds.touch();
    this.stopLocal();
    const generation = this.generation;
    const epoch = this.outputEpoch;
    try {
      this.timing.begin();
      const body = await this.control<{
        presentation: VoiceTurnResponse['presentation'];
        spoken: VoiceSpokenPayload;
      }>('presentation', {
        ...presentation,
        presentation_command: action
      });
      if (body && this.outputIsCurrent(generation, epoch)) {
        this.update({ presentation: body.presentation });
        this.expectSpeech(body.spoken);
      }
    } catch {
      if (this.outputIsCurrent(generation, epoch))
        this.update({ notice: 'tts_timeout', playback: 'text_only' });
    }
  };
  private dropped() {
    this.droppedPending = true;
    this.update({ notice: 'queue_full' });
  }
  submitTranscript = async (transcript: VoiceFinalTranscript) => {
    if (
      !this.snapshot.session ||
      this.snapshot.hidden ||
      transcript.reviewIssue ||
      this.seen.has(transcript.itemId)
    )
      return;
    // Plan C: the focus stamp is captured HERE (enqueue), never re-read at
    // submission — queued words can never be reinterpreted against a focus
    // selection the user had not seen when they spoke.
    this.focusStamps.set(transcript.itemId, this.focusStamp?.() ?? null);
    // Queue wait clock: enqueue -> actual submit, same browser monotonic
    // clock as the timing report. Ephemeral, bounded by the queue.
    this.enqueueClocks.set(transcript.itemId, this.timing.mark());
    if (!this.queue.push(transcript)) {
      this.focusStamps.delete(transcript.itemId);
      this.enqueueClocks.delete(transcript.itemId);
      return;
    }
    this.seen.add(transcript.itemId);
    this.update({ queued: this.queue.length });
    void this.drainQueue();
  };
  private async drainQueue() {
    if (this.submitting) return;
    this.submitting = true;
    const generation = this.generation;
    try {
      while (
        this.queue.length &&
        generation === this.generation &&
        !this.snapshot.hidden
      ) {
        const next = this.queue.shift();
        this.update({ queued: this.queue.length });
        if (next) await this.submitNow(next);
      }
    } finally {
      this.submitting = false;
      if (this.queue.length && generation !== this.generation)
        void this.drainQueue();
      if (
        this.droppedPending &&
        this.snapshot.session &&
        !this.snapshot.hidden
      ) {
        this.droppedPending = false;
        await this.prompt('status', 'queue_dropped');
      }
    }
  }
  private async submitNow(transcript: VoiceFinalTranscript) {
    const active = this.snapshot.session;
    if (!active) return;
    const generation = this.generation;
    const resultEpoch = this.resultEpoch;
    const outputEpoch = this.outputEpoch;
    const focusStamp = this.focusStamps.get(transcript.itemId) ?? null;
    const enqueuedAt = this.enqueueClocks.get(transcript.itemId);
    this.enqueueClocks.delete(transcript.itemId);
    const progressMode = this.preferences().voiceProgressMode;
    const lastSubmitted = {
      itemId: transcript.itemId,
      sessionId: active.id,
      threadId: active.thread_id,
      status: 'pending' as const
    };
    this.update({ lastSubmitted });
    this.bounds.touch();
    const epoch = this.playback.beginTurn();
    // Close the previous turn's timing window before measuring this submit.
    this.timing.finish();
    const clientTiming = this.timing.begin(transcript.itemId, enqueuedAt);
    // A newly requested turn may speak a fixed status before its HTTP result.
    void this.playAudio();
    try {
      const turn = await this.control<VoiceTurnResponse>('turns', {
        transcript: transcript.text,
        item_id: transcript.itemId,
        confidence: transcript.confidence,
        language: transcript.language,
        decision_context: transcript.decisionContext ?? null,
        ...(clientTiming ? { timing: clientTiming } : {}),
        // Plan C: the capture-time focus stamp (omitted when never observed —
        // byte-compatible with historical requests).
        ...(focusStamp != null ? { expected_focus_version: focusStamp } : {}),
        // Reserved progress-mode bridge for the local preference (server
        // default is `brief`): sent only when the preference is set.
        ...(progressMode ? { progress_mode: progressMode } : {})
      });
      if (
        !turn ||
        generation !== this.generation ||
        resultEpoch !== this.resultEpoch
      )
        return;
      this.playback.bindTurn(
        epoch,
        turn.spoken?.utterance_id === turn.pending_decision?.utterance_id
          ? turn.pending_decision
          : null
      );
      this.decisions.getState().applyTurn(active.id, turn);
      if (!turn.pending_decision && turn.decision_event?.kind === 'stale') {
        // Null turn snapshots have no sequence and cannot blindly clear a
        // newer focus. The store's compare-and-set refresh retires the exact
        // expired focus instead; never resubmit the user's turn.
        void this.decisions
          .getState()
          .refresh()
          .catch(() => {});
      }
      this.update({
        partial: null,
        error: ['failed', 'incomplete'].includes(turn.response_state)
          ? { code: 'VOICE_RESPONSE_INCOMPLETE' }
          : null,
        presentation: turn.presentation ?? null,
        lastSubmitted: {
          ...lastSubmitted,
          status:
            turn.response_state === 'complete' ? 'complete' : 'unconfirmed'
        }
      });
      this.listeners.forEach((listener) => listener.onTurnResult?.(turn));
      if (turn.spoken?.channel_closed === true) {
        // Preserve the completed answer and close dead capture explicitly.
        // A fresh session requires user action; never retry the business turn.
        await this.end('voice_restart_required');
        return;
      }
      if (outputEpoch === this.outputEpoch && !this.snapshot.hidden) {
        if (
          this.snapshot.capability?.foreground_session &&
          turn.pending_decision?.state === 'presented' &&
          ['presented', 'review'].includes(turn.decision_event?.kind ?? '')
        )
          await this.readDecision('readback');
        else this.expectSpeech(turn.spoken);
      } else {
        await this.control('cancel').catch(() => {});
        if (this.snapshot.hidden) await this.control('suspend').catch(() => {});
      }
    } catch (error) {
      if (generation !== this.generation || resultEpoch !== this.resultEpoch)
        return;
      if (
        error instanceof VoiceHttpError &&
        (error.code as string) === VOICE_FOCUS_CHANGED
      ) {
        // The utterance's capture-time focus stamp is stale: admission
        // refused the turn (NOT recorded). Focus is not scope — the session
        // and transport STAY alive. The refused words are HELD for user
        // review (never rebound to the new focus, never automatically
        // retried); queued pre-change transcripts are discarded; the UI
        // re-reads the authoritative thread focus.
        this.focusStamps.delete(transcript.itemId);
        this.seen.delete(transcript.itemId);
        this.queue.clear();
        this.focusStamps.clear();
        this.enqueueClocks.clear();
        this.update({
          queued: 0,
          partial: null,
          pendingConfirm: transcript,
          holdPrompt: null,
          lastSubmitted: { ...lastSubmitted, status: 'unconfirmed' }
        });
        this.listeners.forEach((listener) => listener.onFocusStale?.());
        return;
      }
      this.update({
        lastSubmitted: { ...lastSubmitted, status: 'unconfirmed' }
      });
      if (error instanceof VoiceHttpError && error.status < 500) {
        this.update({ error: { code: error.code } });
        if (
          ['VOICE_SESSION_EXPIRED', 'VOICE_SCOPE_CHANGED'].includes(error.code)
        )
          await this.end('ended_away');
      } else this.connectionLost();
    }
  }
  private async requestHoldPrompt(held: VoiceFinalTranscript) {
    if (held.reviewIssue) return;
    const generation = this.generation;
    this.stopLocal();
    const epoch = this.outputEpoch;
    this.update({
      holdPrompt: {
        utteranceId: null,
        spokenSummary: '',
        playbackState: 'pending'
      }
    });
    try {
      const spoken = await this.control<VoiceSpokenPayload>('prompts', {
        kind: 'transcript_review',
        transcript: held.text,
        item_id: `${held.itemId}#${held.revision ?? 1}`
      });
      if (
        !spoken ||
        !this.outputIsCurrent(generation, epoch) ||
        this.snapshot.pendingConfirm !== held ||
        this.snapshot.hidden
      )
        return;
      this.update({
        holdPrompt: {
          utteranceId: spoken.utterance_id,
          spokenSummary: spoken.spoken_summary,
          playbackState:
            spoken.playback_state === 'requested' ? 'requested' : 'pending'
        }
      });
      this.expectSpeech(spoken);
    } catch {
      if (
        !this.outputIsCurrent(generation, epoch) ||
        this.snapshot.pendingConfirm !== held
      )
        return;
      this.update({
        holdPrompt: {
          utteranceId: null,
          spokenSummary: '',
          playbackState: 'failed'
        }
      });
    }
  }
  confirmPending = async () => {
    const held = this.snapshot.pendingConfirm;
    if (!held || held.reviewIssue || this.snapshot.hidden) return;
    if (held === this.warmupHold) {
      // A warm-up confirm is an approved action only once connected, and
      // only while every fence captured when the words were held still
      // holds. A pre-connect confirm is NOT approval: the hold stays
      // visible. When it does send, it sends exactly once (the hold is
      // cleared before submission and item idempotency dedupes).
      if (this.snapshot.transport !== 'connected') return;
      const fence = this.warmupFence;
      const decision = this.decisions.getState().decision;
      const valid =
        !!this.snapshot.session &&
        this.snapshot.session.id === fence?.sessionId &&
        (this.threadId ?? null) === fence?.threadId &&
        (this.focusStamp?.() ?? null) === fence?.focusVersion &&
        (decision?.operation_id ?? null) === fence?.operationId &&
        (decision?.utterance_id ?? null) === fence?.utteranceId;
      this.warmupHold = null;
      this.warmupFence = null;
      if (!valid) {
        this.update({
          pendingConfirm: null,
          holdPrompt: null,
          notice: 'warmup_discarded'
        });
        return;
      }
      this.update({ pendingConfirm: null, holdPrompt: null });
      await this.submitTranscript(held);
      return;
    }
    this.update({ pendingConfirm: null, holdPrompt: null });
    await this.submitTranscript(held);
  };
  discardPending = () => {
    this.warmupHold = null;
    this.warmupFence = null;
    this.update({ pendingConfirm: null, holdPrompt: null });
  };
  /**
   * Plan C: a CONFIRMED conversation-focus change while a session is live
   * (picker Change/Clear on the Talking about chip). Focus is not analysis
   * scope — the session and transport stay alive (no restart; the
   * `VOICE_SCOPE_CHANGED` restart boundary is untouched). Old-context work
   * is invalidated instead: output stops (pending filler/server playback
   * cancelled), queued pre-change transcripts are discarded (never silently
   * rebound), and in-flight turn results are fenced out of the UI (they stay
   * in thread history; nothing is replayed). Capture resumes immediately
   * and stamps the new confirmed version.
   */
  focusChanged = () => {
    this.discardWarmup('warmup_discarded');
    this.queue.clear();
    this.focusStamps.clear();
    this.enqueueClocks.clear();
    this.resultEpoch += 1;
    this.update({
      queued: 0,
      partial: null,
      lastSubmitted:
        this.snapshot.lastSubmitted?.status === 'pending'
          ? { ...this.snapshot.lastSubmitted, status: 'unconfirmed' }
          : this.snapshot.lastSubmitted
    });
    // Timing generations are invalidated with the result fence (plan E
    // keeps the timing modules themselves); stale timing may never be
    // attributed to post-change capture.
    this.refreshTiming();
    if (this.snapshot.session) void this.cancelOutput(true);
  };
  handleEvent = (raw: string) => {
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(raw);
    } catch {
      return;
    }
    if (this.snapshot.hidden || !this.snapshot.session) return;
    const generation = this.generation;
    const sessionId = this.snapshot.session.id;
    this.lifecycleRelay.observe(event, async (observation) => {
      if (
        generation !== this.generation ||
        this.snapshot.session?.id !== sessionId
      )
        return;
      const result = await this.control<{ channel_closed?: boolean }>(
        'lifecycle',
        observation
      );
      if (result?.channel_closed && generation === this.generation) {
        await this.end('voice_restart_required');
      }
    });
    this.timing.event(event);
    this.playback.event(event);
    const type = event.type;
    if (type === 'input_audio_buffer.speech_started') {
      this.playback.stop();
      this.bounds.touch();
      // Preserve the Phase B echo guard: don't pause the shared media element.
      this.update({ playback: 'idle' });
      return;
    }
    if (
      type === 'response.audio.delta' ||
      type === 'output_audio_buffer.started'
    ) {
      window.clearTimeout(this.ttsTimer);
      this.lastAudioAt = Date.now();
      this.update({ playback: this.audio?.paused ? 'paused' : 'playing' });
      if (!this.idleEnding) this.bounds.touch();
      return;
    }
    if (type === 'output_audio_buffer.stopped') {
      // Provider generation completion is not proof of decision playback.
      if (this.decisions.getState().decision?.state !== 'presented')
        this.update({ playback: 'idle' });
      return;
    }
    if (type === 'conversation.item.input_audio_transcription.delta') {
      if (this.snapshot.mic !== 'listening') return;
      this.update({
        partial: {
          text: String(event.delta ?? ''),
          itemId: String(event.item_id ?? '')
        }
      });
      return;
    }
    if (type !== 'conversation.item.input_audio_transcription.completed')
      return;
    const text = String(event.transcript ?? '').trim();
    const command = normalizeDecisionUtterance(text);
    if (VOICE_STOP_RE.test(command)) {
      void this.cancelOutput(false);
      return;
    }
    if (!/[\p{L}\p{N}]/u.test(text) || FILLER.test(text)) return;
    // Finals can arrive after PTT release; track gating prevents new capture.
    if (this.snapshot.muted) return;
    const connected = this.snapshot.transport === 'connected';
    this.bounds.touch();
    if (this.cue('heard'))
      this.timing.acknowledged(String(event.item_id ?? ''));
    if (/^(?:what can i say|help|what are you waiting for)$/.test(command)) {
      if (connected) void this.help();
      return;
    }
    if (command === 'turn sounds off') {
      if (!connected) return;
      this.savePreferences({ voiceEarcons: false });
      this.update({ notice: 'sounds_off' });
      void this.prompt('status', 'sounds_off');
      return;
    }
    if (
      this.snapshot.presentation &&
      !this.snapshot.pendingConfirm &&
      this.decisions.getState().decision?.state !== 'presented'
    ) {
      const commands: Record<string, 'next' | 'repeat' | 'slower' | 'short'> = {
        next: 'next',
        'next three': 'next',
        'more detail': 'next',
        repeat: 'repeat',
        'repeat that': 'repeat',
        slower: 'slower',
        'repeat that more slowly': 'slower',
        'short version': 'short'
      };
      if (commands[command]) {
        if (connected) void this.present(commands[command]);
        return;
      }
    }
    const value: VoiceFinalTranscript = {
      text,
      itemId: String(event.item_id ?? ''),
      confidence:
        typeof event.confidence === 'number' ? event.confidence : null,
      language: this.snapshot.session.locale,
      decisionContext: decisionContext(this.decisions.getState().decision)
    };
    // A repeated provider final for a previously admitted item must not
    // reopen transcript review or become a second request.
    if (!value.itemId || this.seen.has(value.itemId)) return;
    // VOICE-H08: a warm-up final is preserved for visible explicit
    // review/confirmation — never auto-executed or replayed on connection,
    // with no prompt, provider dispatch or business submit while
    // disconnected.
    if (!connected) {
      this.holdWarmup(value, command);
      return;
    }
    const held = this.snapshot.pendingConfirm;
    if (held) {
      if (VOICE_CONFIRM_RE.test(command)) {
        void this.confirmPending();
        return;
      }
      if (VOICE_DISCARD_RE.test(command)) {
        this.discardPending();
        return;
      }
      if (held.itemId === value.itemId && held.text === value.text) return;
      if (!TRANSCRIPT_CORRECTION.test(text)) {
        this.holdConflict(held, value);
        return;
      }
      const revision = {
        ...value,
        revision: (held.revision ?? 1) + 1,
        supersedes: held.itemId
      };
      this.hold(revision);
      return;
    }
    const floor = this.snapshot.capability?.confidence_floor ?? 0.85;
    // Optional review applies to new requests. Existing decision replies and
    // explicit target corrections keep their server-owned guarded path;
    // confirming a held transcript never recursively holds the same words.
    if (
      !(
        this.decisions.getState().decision?.state === 'presented' &&
        (value.confidence === null || value.confidence >= floor) &&
        isVoiceTargetCorrection(text)
      ) &&
      (shouldHoldTranscript(text, value.confidence, floor) ||
        (this.preferences().voiceReviewEveryRequest === true &&
          !isBareDecisionUtterance(text)))
    ) {
      this.hold({ ...value, revision: 1 });
      return;
    }
    this.listeners.forEach((listener) => listener.onFinalTranscript?.(value));
    void this.submitTranscript(value);
  };
  private hold(value: VoiceFinalTranscript) {
    // A connected revision supersedes any warm-up hold as fresh connected
    // capture; the warm-up fences no longer apply to it.
    this.warmupHold = null;
    this.warmupFence = null;
    this.update({
      pendingConfirm: value,
      revisions: [...this.snapshot.revisions, value].slice(-20)
    });
    this.listeners.forEach((listener) => listener.onFinalTranscript?.(value));
    void this.requestHoldPrompt(value);
  }
  private holdConflict(
    held: VoiceFinalTranscript,
    received: VoiceFinalTranscript
  ) {
    const blocked: VoiceFinalTranscript = {
      ...held,
      reviewIssue: 'multiple_segments'
    };
    if (this.warmupHold === held) this.warmupHold = blocked;
    this.update({
      pendingConfirm: blocked,
      holdPrompt: null,
      revisions: [
        ...this.snapshot.revisions,
        { ...received, reviewIssue: 'multiple_segments' as const }
      ].slice(-20)
    });
    this.listeners.forEach((listener) =>
      listener.onFinalTranscript?.(received)
    );
    if (!held.reviewIssue && this.snapshot.transport === 'connected')
      void this.cancelOutput(false);
  }
  /**
   * VOICE-H08: preserve a completed warm-up final for visible explicit
   * review/confirmation while the transport is not connected. No review
   * prompt (or any other request) is sent before connection, and bare
   * pre-connect confirm/discard speech is never an approved action.
   */
  private holdWarmup(value: VoiceFinalTranscript, command: string) {
    if (VOICE_CONFIRM_RE.test(command) || VOICE_DISCARD_RE.test(command))
      return;
    const held = this.snapshot.pendingConfirm;
    if (held && held.itemId === value.itemId && held.text === value.text)
      return;
    if (held && !TRANSCRIPT_CORRECTION.test(value.text)) {
      this.holdConflict(held, value);
      return;
    }
    const revision = held
      ? {
          ...value,
          revision: (held.revision ?? 1) + 1,
          supersedes: held.itemId
        }
      : { ...value, revision: 1 };
    const decision = this.decisions.getState().decision;
    this.warmupHold = revision;
    this.warmupFence = {
      sessionId: this.snapshot.session?.id ?? null,
      threadId: this.threadId ?? null,
      focusVersion: this.focusStamp?.() ?? null,
      operationId: decision?.operation_id ?? null,
      utteranceId: decision?.utterance_id ?? null
    };
    this.update({
      pendingConfirm: revision,
      revisions: [...this.snapshot.revisions, revision].slice(-20),
      holdPrompt: null,
      notice: 'warmup_held'
    });
    this.listeners.forEach((listener) =>
      listener.onFinalTranscript?.(revision)
    );
  }
  /** Mute/hidden/end/logout/focus boundaries discard a held warm-up safely. */
  private discardWarmup(notice: VoiceNotice = null) {
    const held = this.warmupHold;
    this.warmupHold = null;
    this.warmupFence = null;
    if (!held) return;
    if (this.snapshot.pendingConfirm === held)
      this.update({ pendingConfirm: null, holdPrompt: null, notice });
    else if (notice) this.update({ notice });
  }
  private async reportPlayback() {
    if (this.reporting || this.snapshot.hidden) return;
    const generation = this.generation;
    const decision = this.decisions.getState().decision;
    const event = this.playback.next(decision);
    if (!event || !decision?.utterance_id) return;
    this.reporting = true;
    try {
      await this.decisions.getState().decide(event, '', {
        utterance_id: decision.utterance_id,
        spoken_summary_hash: decision.spoken_summary_hash
      });
      if (generation === this.generation && event === 'playback-completed')
        this.update({ playback: 'idle' });
    } catch {
      if (generation === this.generation) this.playback.failed(decision, event);
    } finally {
      if (generation === this.generation) this.reporting = false;
    }
  }
  start = async (reconnecting = false) => {
    const cap = this.snapshot.capability;
    if (
      !cap?.enabled ||
      cap.runtime?.available === false ||
      this.starting ||
      this.permissionPending ||
      this.snapshot.session ||
      document.hidden
    )
      return;
    const p = this.preferences();
    if (
      p.voiceConsentVersion !== cap.consent_version ||
      !cap.voices.some(
        (pair) =>
          pair.voice === p.voiceOutputVoice && pair.locale === p.voiceLocale
      )
    ) {
      this.update({ error: { code: 'VOICE_CONSENT_REQUIRED' } });
      return;
    }
    this.starting = true;
    this.idleEnding = false;
    this.outputEnergy = 0;
    this.timingRtpSource = null;
    const requestedThread = this.threadId ?? null;
    const preflightGeneration = this.generation;
    let preflightFocusVersion: number | null | undefined;
    if (this.contextPreflight) {
      let outcome: ContextPreflightOutcome | null = null;
      try {
        outcome = await this.contextPreflight({
          threadId: requestedThread,
          entry: 'voice'
        });
      } catch {
        outcome = null;
      }
      if (
        !outcome ||
        !outcome.ok ||
        preflightGeneration !== this.generation ||
        (this.threadId ?? null) !== requestedThread
      ) {
        // Failed or stale context: fail closed — no session is minted for an
        // unconfirmed machine target, and a start admitted against the old
        // thread is dropped after a switch (plan 3.1).
        if (preflightGeneration === this.generation) this.starting = false;
        return;
      }
      // The preflight's thread id is authoritative for session creation.
      if (outcome.threadId != null) this.threadId = outcome.threadId;
      preflightFocusVersion = outcome.focusVersion;
    }
    const generation = ++this.generation;
    this.abort = new AbortController();
    this.ears.unlock();
    this.update({
      transport: reconnecting ? 'reconnecting' : 'connecting',
      error: null,
      hidden: false,
      mode: p.voiceListeningMode,
      muted: reconnecting,
      networkDiagnostics: null,
      pendingConfirm: null,
      holdPrompt: null,
      partial: null,
      presentation: null,
      lastSpoken: null,
      revisions: [],
      lastSubmitted: reconnecting ? this.snapshot.lastSubmitted : null,
      notice: reconnecting ? 'reconnecting' : null
    });
    this.queue = new TurnQueue(
      Math.max(1, Math.min(cap.max_queued_turns, 10)),
      () => this.dropped()
    );
    this.warmupHold = null;
    this.warmupFence = null;
    this.bounds = new ForegroundBounds(cap.idle_timeout_s * 1000);
    this.installLifecycle();
    try {
      if (
        typeof RTCPeerConnection === 'undefined' ||
        !navigator.mediaDevices?.getUserMedia
      )
        throw new VoiceHttpError('BROWSER_UNSUPPORTED', 0);
      this.sessionRequestThread = this.threadId ?? null;
      // Plan C: an optional compatible focus stamp (omitted when never
      // observed). A stale stamp refuses the session BEFORE it begins —
      // the focus is re-confirmed through the owner focus PUT first.
      const createFocusStamp =
        preflightFocusVersion !== undefined
          ? preflightFocusVersion
          : (this.focusStamp?.() ?? null);
      const created = await this.request<VoiceSessionPayload>(
        'sessions',
        'POST',
        {
          thread_id: this.threadId ?? null,
          locale: p.voiceLocale,
          voice: p.voiceOutputVoice,
          consent_version: p.voiceConsentVersion,
          ...(createFocusStamp != null
            ? { expected_focus_version: createFocusStamp }
            : {})
        }
      );
      if (generation !== this.generation) {
        endOnPageHide(this.host, created.id);
        return;
      }
      if ((this.threadId ?? null) !== this.sessionRequestThread) {
        // The visible thread changed while this session was being created.
        // A stale start is deleted server-side and never adopted (plan 3.1).
        endOnPageHide(this.host, created.id);
        return;
      }
      this.threadId = created.thread_id;
      this.update({ session: created });
      this.decisions.getState().setSession(created.id);
      this.playback = new DecisionPlayback();
      if (!created.transports_allowed.webrtc)
        throw new VoiceHttpError('VOICE_TRANSPORT_UNAVAILABLE', 0);
      // A browser permission prompt cannot be cancelled. Keep one acquisition
      // in flight even when Stop invalidates the surrounding provider session.
      this.permissionPending = true;
      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true }
        });
      } finally {
        this.permissionPending = false;
      }
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      this.gateMicrophone();
      this.microphoneCleanup = monitorMicrophone(
        stream,
        cap.mic_silence_rms,
        cap.mic_silence_window_s * 1000,
        () =>
          this.snapshot.mic === 'listening' &&
          !this.submitting &&
          !['pending', 'playing'].includes(this.snapshot.playback),
        () => this.update({ notice: 'mic_silent' }),
        () => {
          // An input warning must not survive a recovered microphone or
          // replace a newer connection/playback notice.
          if (this.snapshot.notice === 'mic_silent') {
            this.update({ notice: null });
          }
        }
      );
      this.devices = await navigator.mediaDevices
        .enumerateDevices()
        .catch(() => []);
      if (generation !== this.generation) return;
      const peer = new RTCPeerConnection({ iceServers: cap.ice_servers });
      this.peer = peer;
      stream.getAudioTracks().forEach((track) => {
        peer.addTrack(track, stream);
        track.addEventListener('ended', () => {
          if (generation === this.generation) this.connectionLost();
        });
      });
      peer.addEventListener('track', (event) => {
        if (generation !== this.generation) return;
        this.audio?.pause();
        const audio = new Audio();
        this.audio = audio;
        audio.autoplay = true;
        this.updateOutputs();
        audio.srcObject = event.streams[0] ?? new MediaStream([event.track]);
        // Starting a silent WebRTC media element is not evidence of TTS output.
        void this.playAudio();
      });
      const channel = peer.createDataChannel('voice-live-events');
      this.channel = channel;
      const connected = () => {
        if (generation !== this.generation || channel.readyState !== 'open')
          return;
        window.clearTimeout(this.connectTimer);
        window.clearTimeout(this.reconnectTimer);
        this.update({
          transport: 'connected',
          notice: this.snapshot.hidden
            ? 'hidden'
            : reconnecting
              ? 'reconnected'
              : null
        });
        this.gateMicrophone();
        this.cue('ready');
        this.refreshTiming();
        if (typeof peer.getStats === 'function')
          void peer
            .getStats()
            .then((stats) => {
              if (generation === this.generation)
                this.update({
                  networkDiagnostics: selectedCandidateDiagnostics(stats)
                });
            })
            .catch(() => {
              /* Unavailable diagnostics are not a connectivity claim. */
            });
        void this.acquireWakeLock();
        void this.decisions
          .getState()
          .refresh()
          .then(async () => {
            if (generation !== this.generation || this.snapshot.hidden) return;
            if (
              reconnecting &&
              this.decisions.getState().decision?.operation_id
            )
              await this.readDecision('repeat');
            else if (reconnecting)
              await this.prompt('status', 'connection_dropped');
          })
          .catch(() => {
            if (generation === this.generation)
              this.update({ notice: 'readback_available' });
          });
      };
      channel.addEventListener('open', connected);
      channel.addEventListener('message', (event) => {
        if (generation === this.generation)
          this.handleEvent(String(event.data));
      });
      channel.addEventListener('close', () => {
        if (generation === this.generation) this.connectionLost();
      });
      peer.addEventListener('connectionstatechange', () => {
        if (generation !== this.generation) return;
        if (peer.connectionState === 'connected') connected();
        if (['failed', 'disconnected'].includes(peer.connectionState))
          this.connectionLost();
      });
      await peer.setLocalDescription(await peer.createOffer());
      await new Promise<void>((resolve) => {
        if (peer.iceGatheringState === 'complete') {
          resolve();
          return;
        }
        const done = () => {
          peer.removeEventListener('icegatheringstatechange', changed);
          window.clearTimeout(timer);
          resolve();
        };
        const changed = () => {
          if (peer.iceGatheringState === 'complete') done();
        };
        const timer = window.setTimeout(done, 2500);
        peer.addEventListener('icegatheringstatechange', changed);
      });
      if (generation !== this.generation) return;
      const sdp = await this.control<{ sdp_answer: string }>('sdp', {
        sdp_offer: peer.localDescription?.sdp ?? ''
      });
      if (!sdp || generation !== this.generation) return;
      await peer.setRemoteDescription({ type: 'answer', sdp: sdp.sdp_answer });
      connected();
      if (channel.readyState !== 'open')
        this.connectTimer = window.setTimeout(
          () => this.connectionLost(),
          (cap.direct_connect_timeout_s ?? 12) * 1000
        );
    } catch (error) {
      if (generation !== this.generation) return;
      const code =
        error instanceof VoiceHttpError
          ? error.code
          : error instanceof DOMException && error.name === 'NotAllowedError'
            ? 'MICROPHONE_DENIED'
            : 'VOICE_SIGNALING_FAILED';
      if ((code as string) === VOICE_FOCUS_CHANGED) {
        // The session was refused BEFORE it began: no session exists and no
        // automatic retry is attempted. The UI re-reads the authoritative
        // thread focus and the user re-enters after confirming it.
        this.listeners.forEach((listener) => listener.onFocusStale?.());
      }
      const retry =
        reconnecting &&
        this.reconnectAttempts < cap.reconnect.max_attempts &&
        ![
          'MICROPHONE_DENIED',
          'BROWSER_UNSUPPORTED',
          'VOICE_CONSENT_REQUIRED'
        ].includes(code);
      const endedGeneration = this.generation + 1;
      await this.end(retry ? 'reconnecting' : 'connection_failed', retry);
      if (this.generation !== endedGeneration) return;
      this.update({
        transport: retry ? 'reconnecting' : 'failed',
        error: retry ? null : { code }
      });
      if (retry) {
        this.installLifecycle();
        this.scheduleReconnect();
      }
    } finally {
      if (generation === this.generation) this.starting = false;
    }
  };
  private connectionLost() {
    if (!this.snapshot.session) return;
    // A drop mutes: the mute boundary discards a held warm-up safely.
    this.discardWarmup();
    this.update({
      transport: 'reconnecting',
      muted: true,
      notice: 'reconnecting',
      lastSubmitted:
        this.snapshot.lastSubmitted?.status === 'pending'
          ? { ...this.snapshot.lastSubmitted, status: 'unconfirmed' }
          : this.snapshot.lastSubmitted
    });
    this.held = false;
    this.gateMicrophone();
    this.stopLocal();
    this.releaseWakeLock();
    this.queue.clear();
    this.update({ queued: 0 });
    this.suspension = this.control('suspend').catch(() => null);
    this.enqueueClocks.clear();
    this.scheduleReconnect();
  }
  private scheduleReconnect() {
    if (
      this.reconnectTimer !== undefined ||
      this.snapshot.transport !== 'reconnecting' ||
      document.hidden ||
      !navigator.onLine
    )
      return;
    const cap = this.snapshot.capability;
    this.reconnectTimer = window.setTimeout(
      () => {
        this.reconnectTimer = undefined;
        // The existing data channel recovered during the grace period.
        if (
          this.peer?.connectionState === 'connected' &&
          this.channel?.readyState === 'open'
        ) {
          const generation = this.generation;
          void this.control('suspend')
            .then(async () => {
              if (generation !== this.generation || document.hidden) return;
              await this.decisions.getState().refresh();
              if (generation !== this.generation || document.hidden) return;
              this.update({ transport: 'connected', notice: 'reconnected' });
              this.refreshTiming();
              this.gateMicrophone();
              await this.acquireWakeLock();
            })
            .catch(() => {
              if (generation === this.generation)
                void this.end('connection_failed');
            });
          return;
        }
        if (this.reconnectAttempts >= (cap?.reconnect.max_attempts ?? 2)) {
          void this.end('connection_failed');
          return;
        }
        this.reconnectAttempts++;
        const ending = this.end('reconnecting', true);
        const generation = this.generation;
        void ending.then(() => {
          if (generation === this.generation) return this.start(true);
        });
      },
      (cap?.reconnect.grace_s ?? 3) * 1000
    );
  }
  private releaseMedia() {
    this.lifecycleRelay.reset();
    this.timingGeneration++;
    this.timing.reset();
    this.generation++;
    this.starting = false;
    this.reporting = false;
    this.abort.abort();
    window.clearTimeout(this.connectTimer);
    window.clearTimeout(this.reconnectTimer);
    window.clearTimeout(this.ttsTimer);
    this.connectTimer = this.reconnectTimer = this.ttsTimer = undefined;
    this.microphoneCleanup?.();
    this.microphoneCleanup = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.peer?.close();
    this.peer = null;
    this.channel = null;
    if (this.audio) {
      this.audio.pause();
      this.audio.srcObject = null;
      this.audio = null;
    }
    this.releaseWakeLock();
    this.playback.stop();
  }
  end = async (
    notice: VoiceSnapshot['notice'] = null,
    reconnecting = false
  ) => {
    const active = this.snapshot.session;
    const host = this.host;
    this.stopLocal();
    this.cue('ended');
    this.releaseMedia();
    this.lifecycleCleanup?.();
    this.lifecycleCleanup = null;
    this.queue.clear();
    this.seen.clear();
    this.warmupHold = null;
    this.warmupFence = null;
    this.focusStamps.clear();
    this.enqueueClocks.clear();
    this.sessionRequestThread = null;
    this.held = false;
    if (!reconnecting) this.reconnectAttempts = 0;
    this.decisions.getState().setSession(null);
    this.update({
      session: null,
      networkDiagnostics: null,
      mic: 'off',
      playback: 'idle',
      transport: 'off',
      pendingConfirm: null,
      holdPrompt: null,
      partial: null,
      queued: 0,
      notice,
      lastSubmitted:
        this.snapshot.lastSubmitted?.status === 'pending'
          ? { ...this.snapshot.lastSubmitted, status: 'unconfirmed' }
          : this.snapshot.lastSubmitted,
      outputs: [],
      outputDevice: '',
      outputSelectionSupported: false
    });
    if (active) {
      try {
        await voiceHttp(host, `sessions/${active.id}`, 'DELETE');
      } catch {
        /* The server expiry backstop remains authoritative. Never retry a turn. */
      }
    }
  };
  minimize = () => {
    if (this.snapshot.persistence === 'end_on_close') void this.end();
  };
  logout = () => {
    void this.end();
    this.ears.close();
    this.update({
      revisions: [],
      lastSubmitted: null,
      lastSpoken: null,
      presentation: null
    });
  };
}
