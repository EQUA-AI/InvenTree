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

it('an unbound buffer stop updates ordinary UI without shortening decision delivery', () => {
  let now = 0;
  const playback = new DecisionPlayback(() => now);
  start(playback, false);
  playback.event({
    type: 'response.audio_transcript.done',
    response_id: 'r1',
    transcript: decision.spoken_summary
  });
  playback.event({ type: 'response.audio.done', response_id: 'r1' });
  playback.event({ type: 'output_audio_buffer.stopped' });
  playback.event({
    type: 'response.done',
    response: { id: 'r1', status: 'completed' }
  });
  const spoken = {
    utterance_id: 'u1',
    spoken_summary_hash: 'h1',
    spoken_summary: decision.spoken_summary,
    playback_state: 'requested' as const
  };
  expect(playback.ordinaryOutputStopped(spoken)).toBe(true);
  expect(playback.next(decision)).toBe('playback-started');
  expect(playback.next({ ...decision, delivery_state: 'playing' })).toBeNull();
  now = estimatedDecisionPlaybackMs(decision.spoken_summary);
  expect(playback.next({ ...decision, delivery_state: 'playing' })).toBe(
    'playback-completed'
  );
  playback.beginTurn();
  expect(playback.ordinaryOutputStopped(spoken)).toBe(false);
});

it.each([
  'wrong-text',
  'wrong-metadata',
  'canceled',
  'no-stop',
  'no-completion',
  'ambiguous'
])('ordinary UI does not claim stopped output for %s evidence', (problem) => {
  const playback = new DecisionPlayback();
  start(playback, problem === 'wrong-metadata');
  playback.event({
    type: 'response.audio_transcript.done',
    response_id: 'r1',
    transcript:
      problem === 'wrong-text' ? 'Different output' : decision.spoken_summary
  });
  playback.event({ type: 'response.audio.done', response_id: 'r1' });
  if (problem === 'ambiguous') {
    playback.event({ type: 'response.created', response: { id: 'r2' } });
    playback.event({ type: 'response.audio.delta', response_id: 'r2' });
    playback.event({
      type: 'response.audio_transcript.done',
      response_id: 'r2',
      transcript: decision.spoken_summary
    });
  }
  if (problem !== 'no-stop')
    playback.event({ type: 'output_audio_buffer.stopped' });
  if (problem !== 'no-completion')
    playback.event({
      type: 'response.done',
      response: {
        id: 'r1',
        status: problem === 'canceled' ? 'cancelled' : 'completed'
      }
    });
  expect(
    playback.ordinaryOutputStopped({
      utterance_id: 'u1',
      spoken_summary_hash: problem === 'wrong-metadata' ? 'other-hash' : 'h1',
      spoken_summary: decision.spoken_summary,
      playback_state: 'requested'
    })
  ).toBe(false);
});
