import type { VoicePendingDecision } from '../../../lib/types/Voice';

interface PlaybackItem {
  utterance: string;
  hash: string;
  epoch: number;
  transcript: string | null;
  hasBindingMetadata: boolean;
  started: boolean;
  done: boolean;
  canceled: boolean;
  reportedStart: boolean;
  reportedDone: boolean;
  startedAt: number | null;
  responseComplete: boolean;
  playbackStopped: boolean;
  reportFailures: number;
}

export function estimatedDecisionPlaybackMs(text: string): number {
  return (
    Math.max(
      2,
      text.split(/\s+/).length * 0.6 +
        (text.match(/\d/g)?.length ?? 0) * 0.3 +
        1
    ) * 1000
  );
}

/** Exact persisted speech correlation is delivery evidence, never assent. */
export class DecisionPlayback {
  private items = new Map<string, PlaybackItem>();
  private epoch = 0;
  private binding: VoicePendingDecision | null = null;

  constructor(private now: () => number = () => performance.now()) {}

  stop() {
    for (const item of this.items.values()) item.canceled = true;
    this.epoch += 1;
    this.binding = null;
  }

  beginTurn(): number {
    this.stop();
    return this.epoch;
  }

  bindTurn(epoch: number, decision: VoicePendingDecision | null) {
    if (epoch === this.epoch) this.binding = decision;
  }

  event(event: Record<string, unknown>) {
    const response = event.response as
      | { id?: string; status?: string; metadata?: Record<string, string> }
      | undefined;
    const id = String(event.response_id ?? response?.id ?? '');
    if (!id) return;
    if (event.type === 'response.created') {
      const meta = response?.metadata;
      if (!this.items.has(id)) {
        this.items.set(id, {
          utterance: meta?.aimms_utterance_id ?? '',
          hash: meta?.aimms_spoken_hash ?? '',
          hasBindingMetadata: !!(
            meta?.aimms_utterance_id || meta?.aimms_spoken_hash
          ),
          epoch: this.epoch,
          transcript: null,
          started: false,
          done: false,
          canceled: false,
          reportedStart: false,
          reportedDone: false,
          startedAt: null,
          responseComplete: false,
          playbackStopped: false,
          reportFailures: 0
        });
        if (this.items.size > 32)
          this.items.delete(this.items.keys().next().value!);
      }
    }
    const item = this.items.get(id);
    if (!item || item.canceled) return;
    if (
      event.type === 'response.audio_transcript.done' &&
      typeof event.transcript === 'string'
    ) {
      item.transcript =
        event.transcript.length <= 16000 ? event.transcript : null;
    }
    if (
      event.type === 'response.audio.delta' ||
      event.type === 'response.audio_transcript.delta' ||
      event.type === 'output_audio_buffer.started'
    ) {
      item.started = true;
      item.startedAt ??= this.now();
    }
    if (
      event.type === 'response.audio.done' ||
      event.type === 'output_audio_buffer.stopped'
    )
      item.done = true;
    if (event.type === 'output_audio_buffer.stopped')
      item.playbackStopped = true;
    if (event.type === 'response.done' && response?.status === 'completed')
      item.responseComplete = true;
    if (
      event.type === 'response.done' &&
      response?.status &&
      response.status !== 'completed'
    )
      item.canceled = true;
  }

  failed(decision: VoicePendingDecision, event: string) {
    for (const item of this.items.values()) {
      if (
        item.utterance !== decision.utterance_id ||
        item.hash !== decision.spoken_summary_hash
      )
        continue;
      if (++item.reportFailures > 3) return;
      if (event === 'playback-started') item.reportedStart = false;
      else item.reportedDone = false;
    }
  }

  next(
    decision: VoicePendingDecision | null
  ): 'playback-started' | 'playback-completed' | null {
    if (!decision || decision.state !== 'presented') return null;
    // Azure WebRTC pre-generated speech currently omits response metadata.
    // Bind only an exact FINAL speech transcript created within this HTTP
    // turn's epoch to the persisted utterance returned by that SAME turn.
    // A previous response, repeated text on another turn, mismatched metadata,
    // partial transcript or interrupted request can never supply this binding.
    const binding = this.binding;
    if (
      binding?.utterance_id === decision.utterance_id &&
      binding?.spoken_summary_hash === decision.spoken_summary_hash
    ) {
      const candidates = [...this.items.values()].filter(
        (entry) =>
          !entry.canceled &&
          !entry.hasBindingMetadata &&
          entry.epoch === this.epoch &&
          entry.transcript === binding.spoken_summary &&
          entry.started
      );
      for (const candidate of candidates) {
        candidate.utterance = '';
        candidate.hash = '';
      }
      if (candidates.length === 1) {
        candidates[0].utterance = binding.utterance_id ?? '';
        candidates[0].hash = binding.spoken_summary_hash;
      }
    }
    const item = [...this.items.values()].find(
      (entry) =>
        entry.utterance === decision.utterance_id &&
        entry.hash === decision.spoken_summary_hash
    );
    if (!item || item.canceled || !item.started) return null;
    // A refreshed server snapshot can acknowledge a start whose HTTP reply
    // was lost. It still cannot prove completion or authorize an action.
    if (decision.delivery_state === 'playing') item.reportedStart = true;
    if (!item.reportedStart && decision.delivery_state === 'requested') {
      item.reportedStart = true;
      return 'playback-started';
    }
    if (
      item.done &&
      item.responseComplete &&
      item.reportedStart &&
      !item.reportedDone &&
      decision.delivery_state === 'playing' &&
      // audio.done is GENERATION completion (also emitted on cancellation).
      // Prefer a matching buffer-stop event. Older transports lack it: wait
      // the calibrated conservative duration after first output before
      // reporting estimated delivery, never the much earlier generation end.
      (item.playbackStopped ||
        (item.startedAt !== null &&
          this.now() - item.startedAt >=
            estimatedDecisionPlaybackMs(decision.spoken_summary)))
    ) {
      item.reportedDone = true;
      return 'playback-completed';
    }
    return null;
  }
}
