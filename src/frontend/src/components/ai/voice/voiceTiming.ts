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
};

/** Bounded, ephemeral correlation only. Never records audio or exports speech. */
export class VoiceTiming {
  private epoch: string | null = null;
  private inputs = new Map<string, Input>();
  private outputs = new Map<string, Output>();
  private binding: VoiceSpokenPayload | null = null;
  private submitted: number | undefined;
  private values: VoiceClientTiming = {};
  private reported = false;
  constructor(
    private send: (report: VoiceTimingReport) => void,
    private now = () => performance.now(),
    private wall = () => Date.now()
  ) {}
  reset(epoch: string | null = null) {
    this.epoch =
      typeof epoch === 'string' && /^[a-f0-9]{32}$/.test(epoch) ? epoch : null;
    this.inputs.clear();
    this.stop();
  }
  stop() {
    this.outputs.clear();
    this.binding = null;
    this.submitted = undefined;
    this.values = {};
    this.reported = false;
  }
  begin(itemId?: string) {
    this.stop();
    if (!this.epoch) return undefined;
    this.submitted = this.now();
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
    const id =
      typeof event.response_id === 'string' ? event.response_id : response?.id;
    if (!id || id.length > 128) return;
    if (type === 'response.created' && !this.outputs.has(id)) {
      // More than one output may mean interim speech. Keep all candidates so
      // ambiguity reduces coverage instead of assigning audio to the wrong turn.
      if (this.outputs.size >= 16) {
        this.stop();
        return;
      }
      this.outputs.set(id, {
        utterance: response?.metadata?.aimms_utterance_id,
        hash: response?.metadata?.aimms_spoken_hash
      });
    }
    const item = this.outputs.get(id);
    if (!item) return;
    if (
      type === 'response.audio.delta' ||
      type === 'output_audio_buffer.started'
    )
      item.started = true;
    if (type === 'output_audio_buffer.stopped') item.stopped = true;
    if (
      type === 'response.audio_transcript.done' &&
      typeof event.transcript === 'string' &&
      event.transcript.length <= 16000
    )
      item.text = event.transcript;
    if (type === 'response.done' && response?.status !== 'completed')
      item.canceled = true;
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
    // Aggregate RTP energy has no response ID: only one unambiguous candidate.
    if (!this.epoch || this.outputs.size !== 1) return;
    const item = [...this.outputs.values()][0];
    if (
      !item.started ||
      item.stopped ||
      item.canceled ||
      item.observed !== undefined
    )
      return;
    item.observed = this.now();
    item.wall = this.wall();
    this.flush();
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
    if (!this.epoch || !binding || this.reported || this.outputs.size !== 1)
      return;
    const item = [...this.outputs.values()][0];
    if (
      item.canceled ||
      item.observed === undefined ||
      item.text !== binding.spoken_summary
    )
      return;
    if (
      (item.utterance || item.hash) &&
      (item.utterance !== binding.utterance_id ||
        item.hash !== binding.spoken_summary_hash)
    )
      return;
    const elapsed = interval(item.observed, this.submitted);
    if (elapsed === undefined) return;
    this.reported = true;
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
