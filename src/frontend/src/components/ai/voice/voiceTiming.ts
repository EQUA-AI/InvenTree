import type {
  VoiceClientTiming,
  VoiceTimingReport
} from '../../../../lib/types/AimmsWire.generated';
import type { VoiceSpokenPayload } from '../../../../lib/types/Voice';

const interval = (end: number, start: number | undefined) => {
  const value = start === undefined ? undefined : end - start;
  return value !== undefined &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= 300000
    ? value
    : undefined;
};
type Input = { end?: number; final?: number; ack?: number };
type Output = {
  text?: string;
  utterance?: string;
  hash?: string;
  canceled?: boolean;
  started?: boolean;
  stopped?: boolean;
  observed?: number;
  wall?: number;
  ambiguous?: boolean;
  reason?: TimingDiagnostic;
};

export type TimingDiagnostic =
  | 'inactive'
  | 'awaiting_energy'
  | 'missing_baseline'
  | 'crossed_output_boundary'
  | 'overlapping_output'
  | 'incomplete_history'
  | 'repeated_text'
  | 'binding_mismatch'
  | 'canceled_output'
  | 'no_correlated_energy'
  | 'reported';

/** Bounded, ephemeral correlation only. Never records audio or exports speech. */
export class VoiceTiming {
  private epoch: string | null = null;
  private inputs = new Map<string, Input>();
  private outputs = new Map<string, Output>();
  private binding: VoiceSpokenPayload | null = null;
  private submitted: number | undefined;
  private values: VoiceClientTiming = {};
  private reported = false;
  private contextVersion = 0;
  get samplingContext() {
    return this.contextVersion;
  }
  private totalEnergy: number | undefined;
  private boundaryPending = false;
  private bufferOwner: string | null = null;
  private diagnostic: TimingDiagnostic = 'inactive';
  constructor(
    private send: (report: VoiceTimingReport) => void,
    private now = () => performance.now(),
    private wall = () => Date.now(),
    private diagnose: (reason: TimingDiagnostic) => void = () => {}
  ) {}
  reset(epoch: string | null = null) {
    this.epoch =
      typeof epoch === 'string' && /^[a-f0-9]{32}$/.test(epoch) ? epoch : null;
    this.inputs.clear();
    this.stop();
  }
  stop() {
    this.contextVersion++;
    this.outputs.clear();
    this.binding = null;
    this.submitted = undefined;
    this.values = {};
    this.reported = false;
    this.totalEnergy = undefined;
    this.boundaryPending = false;
    this.bufferOwner = null;
    this.note('inactive');
  }
  begin(itemId?: string) {
    this.stop();
    if (!this.epoch) return undefined;
    this.submitted = this.now();
    this.note('awaiting_energy');
    const item = itemId ? this.inputs.get(itemId) : undefined;
    this.values = {
      speech_to_final_ms:
        item?.final === undefined ? undefined : interval(item.final, item.end),
      final_to_submit_ms: interval(this.submitted, item?.final),
      ack_schedule_ms:
        item?.ack === undefined ? undefined : interval(item.ack, item.end)
    };
    return this.values;
  }
  acknowledged(itemId: string) {
    const item = this.inputs.get(itemId);
    if (item) item.ack ??= this.now();
  }
  event(event: Record<string, unknown>) {
    if (!this.epoch) return;
    const type = event.type;
    const itemId = typeof event.item_id === 'string' ? event.item_id : '';
    if (
      itemId &&
      itemId.length <= 128 &&
      (type === 'input_audio_buffer.speech_stopped' ||
        type === 'conversation.item.input_audio_transcription.completed')
    ) {
      const item = this.inputs.get(itemId) ?? {};
      if (type === 'input_audio_buffer.speech_stopped') item.end ??= this.now();
      else item.final ??= this.now();
      this.inputs.set(itemId, item);
      if (this.inputs.size > 32)
        this.inputs.delete(this.inputs.keys().next().value!);
    }
    if (this.submitted === undefined) return;
    const response = event.response as
      | { id?: string; status?: string; metadata?: Record<string, string> }
      | undefined;
    let id =
      typeof event.response_id === 'string' ? event.response_id : response?.id;
    if (!id && type === 'output_audio_buffer.started') {
      const candidates = this.active();
      if (candidates.length !== 1) return;
      id = candidates[0][0];
      this.bufferOwner = id;
    }
    if (!id && type === 'output_audio_buffer.stopped') {
      // Ordered channel lifecycle may close only the window it opened.
      // An unbound stop alone must never retire a newer candidate.
      id = this.bufferOwner ?? undefined;
    }
    if (!id || id.length > 128) return;
    if (type === 'response.created' && !this.outputs.has(id)) {
      if (this.outputs.size >= 16) {
        this.stop();
        return;
      }
      const active = this.active();
      for (const [, previous] of active) {
        previous.ambiguous = true;
        previous.reason = 'overlapping_output';
      }
      this.outputs.set(id, {
        utterance: response?.metadata?.aimms_utterance_id,
        hash: response?.metadata?.aimms_spoken_hash,
        ambiguous: active.length > 0 || this.boundaryPending,
        reason: active.length
          ? 'overlapping_output'
          : this.boundaryPending
            ? 'crossed_output_boundary'
            : undefined
      });
      if (active.length) this.note('overlapping_output');
      else if (this.boundaryPending) this.note('crossed_output_boundary');
    }
    const item = this.outputs.get(id);
    if (!item) return;
    if (
      type === 'response.audio.delta' ||
      type === 'output_audio_buffer.started'
    )
      item.started = true;
    if (type === 'output_audio_buffer.stopped' && !item.stopped) {
      item.stopped = true;
      if (this.bufferOwner === id) this.bufferOwner = null;
      // Provider completion is not proof of drained RTP. A subsequent quiet
      // energy interval, before another output, is required to reuse the rail.
      this.boundaryPending = true;
    }
    if (
      type === 'response.audio_transcript.done' &&
      typeof event.transcript === 'string' &&
      event.transcript.length <= 16000
    )
      item.text = event.transcript;
    if (type === 'response.done' && response?.status !== 'completed') {
      item.canceled = true;
      this.boundaryPending = true;
    }
    this.flush();
  }
  bind(spoken: VoiceSpokenPayload | null) {
    this.binding =
      spoken?.playback_state === 'requested' &&
      typeof spoken.utterance_id === 'string' &&
      /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(
        spoken.utterance_id
      ) &&
      typeof spoken.spoken_summary_hash === 'string' &&
      /^[a-f0-9]{64}$/.test(spoken.spoken_summary_hash) &&
      typeof spoken.spoken_summary === 'string' &&
      spoken.spoken_summary.length <= 16000
        ? spoken
        : null;
    this.flush();
  }
  energy() {
    // Consume a verified increase in a single window. Production callers use
    // sampleEnergy, which also verifies the interval's drain/baseline boundary.
    const active = this.active();
    if (!this.epoch || active.length !== 1) return;
    const item = active[0][1];
    if (
      !item.started ||
      item.stopped ||
      item.canceled ||
      item.ambiguous ||
      item.observed !== undefined
    )
      return;
    item.observed = this.now();
    item.wall = this.wall();
    this.flush();
  }
  sampleEnergy(total: number, context = this.contextVersion) {
    if (context !== this.contextVersion) return;
    if (
      !this.epoch ||
      this.submitted === undefined ||
      !Number.isFinite(total) ||
      total < 0
    )
      return;
    const previous = this.totalEnergy;
    this.totalEnergy = total;
    const active = this.active();
    if (previous === undefined || total < previous) {
      // Zero is a genuine clean initial baseline. A positive first sample or
      // counter reset cannot establish this output's original first onset.
      if (total !== 0 || (previous !== undefined && total < previous)) {
        this.boundaryPending = true;
        for (const [, item] of active) {
          item.ambiguous = true;
          item.reason = 'missing_baseline';
        }
        this.note('missing_baseline');
      }
      return;
    }
    if (!active.length) {
      if (total === previous) this.boundaryPending = false;
      else this.boundaryPending = true;
      return;
    }
    if (total > previous) {
      if (this.boundaryPending) {
        for (const [, item] of active) {
          item.ambiguous = true;
          item.reason = 'crossed_output_boundary';
        }
        this.note('crossed_output_boundary');
      } else this.energy();
    }
  }
  private active() {
    return [...this.outputs.entries()].filter(
      ([, item]) => !item.stopped && !item.canceled
    );
  }
  invalidateEnergy() {
    if (!this.epoch || this.submitted === undefined) return;
    this.totalEnergy = undefined;
    this.boundaryPending = true;
    for (const [, item] of this.active()) {
      item.ambiguous = true;
      item.reason = 'missing_baseline';
    }
    this.note('missing_baseline');
  }
  private note(reason: TimingDiagnostic) {
    if (reason === this.diagnostic) return;
    this.diagnostic = reason;
    try {
      this.diagnose(reason);
    } catch {
      /* Diagnostics cannot affect speech. */
    }
  }
  localStop(started: number) {
    const value = interval(this.now(), started);
    if (this.epoch && this.binding && value !== undefined)
      this.emit({
        epoch: this.epoch,
        utterance_id: this.binding.utterance_id,
        spoken_hash: this.binding.spoken_summary_hash,
        provenance: 'local_pause_proxy',
        timing: { local_stop_ms: value }
      });
    this.stop();
  }
  private emit(report: VoiceTimingReport) {
    try {
      this.send(report);
    } catch {
      /* Never retry the business action. */
    }
  }
  private flush() {
    const binding = this.binding;
    if (!this.epoch || !binding || this.reported) return;
    const outputs = [...this.outputs.values()];
    if (outputs.some((item) => item.text === undefined)) {
      this.note('incomplete_history');
      return;
    }
    const matches = outputs.filter(
      (item) => item.text === binding.spoken_summary
    );
    if (matches.length !== 1) {
      this.note(matches.length > 1 ? 'repeated_text' : 'binding_mismatch');
      return;
    }
    const item = matches[0];
    if (item.canceled) {
      this.note('canceled_output');
      return;
    }
    if (item.ambiguous) {
      this.note(item.reason ?? 'overlapping_output');
      return;
    }
    if (item.observed === undefined) {
      this.note(item.stopped ? 'no_correlated_energy' : 'awaiting_energy');
      return;
    }
    if (
      (item.utterance || item.hash) &&
      (item.utterance !== binding.utterance_id ||
        item.hash !== binding.spoken_summary_hash)
    ) {
      this.note('binding_mismatch');
      return;
    }
    const elapsed = interval(item.observed, this.submitted);
    if (elapsed === undefined) return;
    this.reported = true;
    this.note('reported');
    this.emit({
      epoch: this.epoch,
      utterance_id: binding.utterance_id,
      spoken_hash: binding.spoken_summary_hash,
      provenance: 'rtp_energy_proxy',
      first_playback_epoch_ms: item.wall,
      timing: { ...this.values, submit_to_observed_playback_ms: elapsed }
    });
  }
}
