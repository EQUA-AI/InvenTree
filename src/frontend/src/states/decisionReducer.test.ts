import { describe, expect, it } from 'vitest';
import type { VoicePendingDecision } from '../../lib/types/Voice';
import { decisionOutcome } from '../components/ai/decisionOutcome';
import { DecisionPlayback } from '../components/ai/decisionPlayback';
import {
  decisionContext,
  decisionReducer,
  emptyDecisionState
} from './decisionReducer';

const decision = (id = 'd', sequence = 1) =>
  ({
    decision_id: id,
    sequence,
    revision: 3,
    preview_hash: 'hash',
    state: 'presented',
    utterance_id: 'u',
    spoken_summary_hash: 's',
    spoken_summary: 'Confirm hold or cancel.',
    delivery_state: 'requested'
  }) as VoicePendingDecision;

describe('decision focus', () => {
  it('keeps completed, failed and unknown portions distinct', () => {
    expect(
      decisionOutcome({ completed: ['A'], failed: ['B'], unknown: ['C'] })
    ).toEqual({ completed: ['A'], failed: ['B'], unknown: ['C'] });
    expect(decisionOutcome(null)).toEqual({
      completed: [],
      failed: [],
      unknown: []
    });
  });
  it('carries only exact focus references', () => {
    expect(decisionContext(decision())).toEqual({
      decision_id: 'd',
      sequence: 1,
      revision: 3,
      preview_hash: 'hash'
    });
  });
  it('ignores old sessions and stale sequences', () => {
    const base = {
      ...emptyDecisionState,
      sessionId: 'session',
      decision: decision('d', 4)
    };
    expect(decisionReducer(base, 'old', { pending_decision: decision() })).toBe(
      base
    );
    expect(
      decisionReducer(base, 'session', { pending_decision: decision() })
    ).toBe(base);
  });
  it('does not rearm a retired target', () => {
    const base = {
      ...emptyDecisionState,
      sessionId: 'session',
      decision: decision()
    };
    const next = decisionReducer(base, 'session', {
      pending_decision: decision('new')
    });
    expect(
      decisionReducer(next, 'session', { pending_decision: decision('d', 99) })
    ).toBe(next);
  });
});

describe('bound playback', () => {
  const metadataFreeResponse = (
    tracker: DecisionPlayback,
    id = 'r',
    text = decision().spoken_summary
  ) => {
    tracker.event({ type: 'response.created', response: { id } });
    tracker.event({ type: 'response.audio_transcript.delta', response_id: id });
    tracker.event({
      type: 'response.audio_transcript.done',
      response_id: id,
      transcript: text
    });
    tracker.event({ type: 'response.audio.done', response_id: id });
    tracker.event({
      type: 'response.done',
      response: { id, status: 'completed' }
    });
  };
  it('correlates metadata-free Azure speech only to the exact originating turn', () => {
    let now = 0;
    const tracker = new DecisionPlayback(() => now);
    const epoch = tracker.beginTurn();
    metadataFreeResponse(tracker);
    expect(tracker.next(decision())).toBeNull();
    tracker.bindTurn(epoch, decision());
    expect(tracker.next(decision())).toBe('playback-started');
    // Real WebRTC buffer events have no response id: not drain proof.
    tracker.event({ type: 'output_audio_buffer.stopped' });
    expect(
      tracker.next({ ...decision(), delivery_state: 'playing' })
    ).toBeNull();
    now = 4000;
    expect(tracker.next({ ...decision(), delivery_state: 'playing' })).toBe(
      'playback-completed'
    );
  });
  it('rejects old identical speech and a response to an interrupted HTTP turn', () => {
    const tracker = new DecisionPlayback();
    const oldEpoch = tracker.beginTurn();
    metadataFreeResponse(tracker);
    const newEpoch = tracker.beginTurn();
    tracker.bindTurn(oldEpoch, decision());
    expect(tracker.next(decision())).toBeNull();
    tracker.bindTurn(newEpoch, decision());
    expect(tracker.next(decision())).toBeNull();
    tracker.stop();
    metadataFreeResponse(tracker, 'late');
    tracker.bindTurn(newEpoch, decision());
    expect(tracker.next(decision())).toBeNull();
  });
  it('rejects partial, changed, ambiguous and mismatched-metadata speech', () => {
    const tracker = new DecisionPlayback();
    tracker.bindTurn(tracker.beginTurn(), decision());
    metadataFreeResponse(tracker, 'different', 'Different text');
    tracker.event({ type: 'response.created', response: { id: 'partial' } });
    tracker.event({
      type: 'response.audio_transcript.delta',
      response_id: 'partial',
      delta: decision().spoken_summary
    });
    expect(tracker.next(decision())).toBeNull();
    metadataFreeResponse(tracker, 'a');
    metadataFreeResponse(tracker, 'b');
    expect(tracker.next(decision())).toBeNull();
    const another = new DecisionPlayback();
    another.bindTurn(another.beginTurn(), decision());
    another.event({
      type: 'response.created',
      response: {
        id: 'bad',
        metadata: { aimms_utterance_id: 'wrong', aimms_spoken_hash: 's' }
      }
    });
    metadataFreeResponse(another, 'bad');
    expect(another.next(decision())).toBeNull();
  });
  const started = () => {
    const tracker = new DecisionPlayback();
    tracker.event({
      type: 'response.created',
      response: {
        id: 'r',
        metadata: { aimms_utterance_id: 'u', aimms_spoken_hash: 's' }
      }
    });
    return tracker;
  };
  it('never completes a stopped-before-start item', () => {
    const tracker = started();
    tracker.stop();
    tracker.event({ type: 'response.audio.done', response_id: 'r' });
    expect(tracker.next(decision())).toBeNull();
  });
  it('requires matching metadata and monotonic start before completion', () => {
    const tracker = started();
    tracker.event({
      type: 'response.audio_transcript.delta',
      response_id: 'r'
    });
    tracker.event({ type: 'response.audio.done', response_id: 'r' });
    expect(tracker.next({ ...decision(), utterance_id: 'other' })).toBeNull();
    expect(tracker.next(decision())).toBe('playback-started');
    expect(tracker.next(decision())).toBeNull();
    // Generation completion alone is not completed playback.
    expect(
      tracker.next({ ...decision(), delivery_state: 'playing' })
    ).toBeNull();
    tracker.event({
      type: 'response.done',
      response: { id: 'r', status: 'completed' }
    });
    tracker.event({ type: 'output_audio_buffer.stopped', response_id: 'r' });
    expect(tracker.next({ ...decision(), delivery_state: 'playing' })).toBe(
      'playback-completed'
    );
    expect(
      tracker.next({ ...decision(), delivery_state: 'playing' })
    ).toBeNull();
  });

  it('does not complete an interrupted generation, even after audio.done', () => {
    const tracker = started();
    tracker.event({ type: 'response.audio.delta', response_id: 'r' });
    tracker.event({ type: 'response.audio.done', response_id: 'r' });
    expect(tracker.next(decision())).toBe('playback-started');
    tracker.event({
      type: 'response.done',
      response: { id: 'r', status: 'cancelled' }
    });
    tracker.event({ type: 'output_audio_buffer.stopped', response_id: 'r' });
    expect(
      tracker.next({ ...decision(), delivery_state: 'playing' })
    ).toBeNull();
  });

  it('waits a conservative delivery duration when buffer events are absent', () => {
    let now = 0;
    const tracker = new DecisionPlayback(() => now);
    tracker.event({
      type: 'response.created',
      response: {
        id: 'r',
        metadata: { aimms_utterance_id: 'u', aimms_spoken_hash: 's' }
      }
    });
    tracker.event({ type: 'response.audio.delta', response_id: 'r' });
    expect(tracker.next(decision())).toBe('playback-started');
    tracker.event({ type: 'response.audio.done', response_id: 'r' });
    tracker.event({
      type: 'response.done',
      response: { id: 'r', status: 'completed' }
    });
    now = 1000;
    expect(
      tracker.next({ ...decision(), delivery_state: 'playing' })
    ).toBeNull();
    now = 4000;
    expect(tracker.next({ ...decision(), delivery_state: 'playing' })).toBe(
      'playback-completed'
    );
  });
});
