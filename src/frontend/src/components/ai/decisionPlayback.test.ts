import { expect, it } from 'vitest';
import type { VoicePendingDecision } from '../../../lib/types/Voice';
import {
  DecisionPlayback,
  estimatedDecisionPlaybackMs
} from './decisionPlayback';

const decision = {
  state: 'presented',
  utterance_id: 'u1',
  spoken_summary_hash: 'h1',
  spoken_summary: 'Confirm hold for work order 104.',
  delivery_state: 'requested'
} as VoicePendingDecision;

function start(playback: DecisionPlayback, metadata = true) {
  const epoch = playback.beginTurn();
  playback.bindTurn(epoch, decision);
  playback.event({
    type: 'response.created',
    response: {
      id: 'r1',
      ...(metadata
        ? { metadata: { aimms_utterance_id: 'u1', aimms_spoken_hash: 'h1' } }
        : {})
    }
  });
  playback.event({ type: 'response.audio.delta', response_id: 'r1' });
}

it('generation completion alone never proves a fully heard decision', () => {
  let now = 0;
  const playback = new DecisionPlayback(() => now);
  start(playback);
  expect(playback.next(decision)).toBe('playback-started');
  const playing = { ...decision, delivery_state: 'playing' as const };
  playback.event({ type: 'response.audio.done', response_id: 'r1' });
  playback.event({
    type: 'response.done',
    response: { id: 'r1', status: 'completed' }
  });
  expect(playback.next(playing)).toBeNull();
  now = estimatedDecisionPlaybackMs(decision.spoken_summary);
  expect(playback.next(playing)).toBe('playback-completed');
  expect(playback.next(playing)).toBeNull();
});

it('metadata-free fallback requires the exact final transcript in the same epoch', () => {
  const playback = new DecisionPlayback();
  start(playback, false);
  expect(playback.next(decision)).toBeNull();
  playback.event({
    type: 'response.audio_transcript.done',
    response_id: 'r1',
    transcript: 'different action'
  });
  expect(playback.next(decision)).toBeNull();
  playback.event({
    type: 'response.audio_transcript.done',
    response_id: 'r1',
    transcript: decision.spoken_summary
  });
  expect(playback.next(decision)).toBe('playback-started');
  playback.stop();
  playback.event({ type: 'output_audio_buffer.stopped', response_id: 'r1' });
  playback.event({
    type: 'response.done',
    response: { id: 'r1', status: 'completed' }
  });
  expect(playback.next({ ...decision, delivery_state: 'playing' })).toBeNull();
});

it('a cancelled response cannot supply delivery evidence', () => {
  const playback = new DecisionPlayback();
  start(playback);
  playback.event({
    type: 'response.done',
    response: { id: 'r1', status: 'cancelled' }
  });
  expect(playback.next(decision)).toBeNull();
});
