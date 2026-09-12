import type { VoicePendingDecision } from '../../../lib/types/Voice';

interface PlaybackItem {
  utterance: string;
  hash: string;
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

/** Provider metadata binds delivery to the exact persisted utterance, never assent. */
export class DecisionPlayback {
  private items = new Map<string, PlaybackItem>();

  constructor(private now: () => number = () => performance.now()) {}

  stop() {
    for (const item of this.items.values()) item.canceled = true;
  }

  event(event: Record<string, unknown>) {
    const response = event.response as
      | { id?: string; status?: string; metadata?: Record<string, string> }
      | undefined;
    const id = String(event.response_id ?? response?.id ?? '');
    if (!id) return;
    if (event.type === 'response.created') {
      const meta = response?.metadata;
      if (meta?.aimms_utterance_id && meta.aimms_spoken_hash) {
        this.items.set(id, {
          utterance: meta.aimms_utterance_id,
          hash: meta.aimms_spoken_hash,
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
            Math.max(
              2,
              decision.spoken_summary.split(/\s+/).length * 0.6 +
                (decision.spoken_summary.match(/\d/g)?.length ?? 0) * 0.3 +
                1
            ) *
              1000))
    ) {
      item.reportedDone = true;
      return 'playback-completed';
    }
    return null;
  }
}
