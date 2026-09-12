import type { VoicePendingDecision } from '../../../lib/types/Voice';

interface PlaybackItem {
  utterance: string;
  hash: string;
  started: boolean;
  done: boolean;
  canceled: boolean;
  reportedStart: boolean;
  reportedDone: boolean;
}

/** Provider metadata binds delivery to the exact persisted utterance, never assent. */
export class DecisionPlayback {
  private items = new Map<string, PlaybackItem>();

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
          reportedDone: false
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
    )
      item.started = true;
    if (
      event.type === 'response.audio.done' ||
      event.type === 'output_audio_buffer.stopped'
    )
      item.done = true;
    if (
      event.type === 'response.done' &&
      response?.status &&
      response.status !== 'completed'
    )
      item.canceled = true;
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
    if (!item.reportedStart && decision.delivery_state === 'requested') {
      item.reportedStart = true;
      return 'playback-started';
    }
    if (
      item.done &&
      item.reportedStart &&
      !item.reportedDone &&
      decision.delivery_state === 'playing'
    ) {
      item.reportedDone = true;
      return 'playback-completed';
    }
    return null;
  }
}
