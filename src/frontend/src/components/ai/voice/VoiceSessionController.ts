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
import { DecisionPlayback } from '../decisionPlayback';
import {
  VOICE_CONFIRM_RE,
  VOICE_DISCARD_RE,
  VOICE_STOP_RE,
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
  type VoicePreferences,
  type VoiceSnapshot,
  legacyClientState
} from './types';
import { VoiceHttpError, endOnPageHide, voiceHttp } from './voiceHttp';
import { ForegroundBounds, installVoiceLifecycle } from './voiceLifecycle';

const TERMINAL = ['ended', 'expired', 'failed'];
const FILLER = /^(?:uh|um|hmm+|mm+|mhm|huh|erm|ah|oh)[.!?,\s]*$/i;
export class VoiceSessionController {
  private host = '';
  private threadId: string | undefined;
  private peer: RTCPeerConnection | null = null;
  private channel: RTCDataChannel | null = null;
  private stream: MediaStream | null = null;
  private audio: HTMLAudioElement | null = null;
  private generation = 0;
  private starting = false;
  private submitting = false;
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
  private idleEnding = false;
  private outputEnergy = 0;
  private lastAudioAt = 0;
  readonly listeners = new Set<{
    onTurnResult?: (turn: VoiceTurnResponse) => void;
    onFinalTranscript?: (value: VoiceFinalTranscript) => void;
  }>();
  constructor(
    private store: StoreApi<VoiceSnapshot>,
    private decisions: Pick<StoreApi<VoiceDecisionStore>, 'getState'>,
    private preferences: () => VoicePreferences,
    private savePreferences: (values: Partial<VoicePreferences>) => void
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
    if (!this.snapshot.session) this.threadId = threadId;
  }
  setCapability(capability: VoiceCapability | null) {
    this.update({
      capability,
      persistence: capability?.foreground_session
        ? 'keep_on_minimize'
        : 'end_on_close'
    });
    if (!capability?.enabled && this.snapshot.session) void this.end();
  }
  private request<T>(path: string, method = 'GET', body?: unknown) {
    return voiceHttp<T>(this.host, path, method, body, this.abort.signal);
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
    if (this.starting && this.snapshot.transport !== 'connected') {
      void this.end('ended_away');
      return;
    }
    this.bounds.hide();
    this.held = false;
    this.update({ hidden: true, notice: 'hidden' });
    this.gateMicrophone();
    this.stopLocal();
    this.releaseWakeLock();
    this.queue.clear();
    this.update({ queued: 0 });
    // Set-aside does not require a possibly stale client decision snapshot.
    this.suspension = this.control('suspend').catch(() => null);
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
    try {
      const stats = await peer.getStats();
      if (peer !== this.peer || this.snapshot.hidden || this.audio?.paused)
        return;
      let energy = 0;
      stats.forEach((report) => {
        if (report.type === 'inbound-rtp' && report.kind === 'audio')
          energy += Number(report.totalAudioEnergy ?? 0);
      });
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
    this.update({ lastSpoken: spoken });
    window.clearTimeout(this.ttsTimer);
    if (spoken?.playback_state !== 'requested') {
      this.update({ playback: spoken ? 'text_only' : 'idle' });
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
  cancel = async () => {
    this.stopLocal(); // Always before any network/queue wait.
    this.bounds.touch();
    try {
      await this.control('cancel');
    } catch {
      /* Local stop is already complete. */
    }
  };
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
      this.seen.has(transcript.itemId)
    )
      return;
    if (!this.queue.push(transcript)) return;
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
    const outputEpoch = this.outputEpoch;
    const lastSubmitted = {
      itemId: transcript.itemId,
      sessionId: active.id,
      threadId: active.thread_id,
      status: 'pending' as const
    };
    this.update({ lastSubmitted });
    this.bounds.touch();
    const epoch = this.playback.beginTurn();
    // A newly requested turn may speak a fixed status before its HTTP result.
    void this.playAudio();
    try {
      const turn = await this.control<VoiceTurnResponse>('turns', {
        transcript: transcript.text,
        item_id: transcript.itemId,
        confidence: transcript.confidence,
        language: transcript.language,
        decision_context: transcript.decisionContext ?? null
      });
      if (!turn || generation !== this.generation) return;
      this.playback.bindTurn(
        epoch,
        turn.spoken?.utterance_id === turn.pending_decision?.utterance_id
          ? turn.pending_decision
          : null
      );
      this.decisions.getState().applyTurn(active.id, turn);
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
      if (generation !== this.generation) return;
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
    if (!held || this.snapshot.hidden) return;
    this.update({ pendingConfirm: null, holdPrompt: null });
    await this.submitTranscript(held);
  };
  discardPending = () => {
    this.update({ pendingConfirm: null, holdPrompt: null });
  };
  handleEvent = (raw: string) => {
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(raw);
    } catch {
      return;
    }
    if (this.snapshot.hidden || !this.snapshot.session) return;
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
      void this.cancel();
      return;
    }
    if (!/[\p{L}\p{N}]/u.test(text) || FILLER.test(text)) return;
    // Finals can arrive after PTT release; track gating prevents new capture.
    if (this.snapshot.muted || this.snapshot.transport !== 'connected') return;
    this.bounds.touch();
    this.cue('heard');
    if (/^(?:what can i say|help|what are you waiting for)$/.test(command)) {
      void this.help();
      return;
    }
    if (command === 'turn sounds off') {
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
        void this.present(commands[command]);
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
    if (!value.itemId) return;
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
      const revision = {
        ...value,
        revision: (held.revision ?? 1) + 1,
        supersedes: held.itemId
      };
      this.hold(revision);
      return;
    }
    const floor = this.snapshot.capability?.confidence_floor ?? 0.85;
    if (
      !(
        this.decisions.getState().decision?.state === 'presented' &&
        (value.confidence === null || value.confidence >= floor) &&
        isVoiceTargetCorrection(text)
      ) &&
      shouldHoldTranscript(text, value.confidence, floor)
    ) {
      this.hold({ ...value, revision: 1 });
      return;
    }
    this.listeners.forEach((listener) => listener.onFinalTranscript?.(value));
    void this.submitTranscript(value);
  };
  private hold(value: VoiceFinalTranscript) {
    this.update({
      pendingConfirm: value,
      revisions: [...this.snapshot.revisions, value].slice(-20)
    });
    this.listeners.forEach((listener) => listener.onFinalTranscript?.(value));
    void this.requestHoldPrompt(value);
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
      this.starting ||
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
    this.bounds = new ForegroundBounds(cap.idle_timeout_s * 1000);
    this.installLifecycle();
    try {
      if (
        typeof RTCPeerConnection === 'undefined' ||
        !navigator.mediaDevices?.getUserMedia
      )
        throw new VoiceHttpError('BROWSER_UNSUPPORTED', 0);
      const created = await this.request<VoiceSessionPayload>(
        'sessions',
        'POST',
        {
          thread_id: this.threadId ?? null,
          locale: p.voiceLocale,
          voice: p.voiceOutputVoice,
          consent_version: p.voiceConsentVersion
        }
      );
      if (generation !== this.generation) {
        endOnPageHide(this.host, created.id);
        return;
      }
      this.threadId = created.thread_id;
      this.update({ session: created });
      this.decisions.getState().setSession(created.id);
      this.playback = new DecisionPlayback();
      if (!created.transports_allowed.webrtc)
        throw new VoiceHttpError('VOICE_TRANSPORT_UNAVAILABLE', 0);
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true }
      });
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
        () => this.snapshot.mic === 'listening',
        () => this.update({ notice: 'mic_silent' })
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
