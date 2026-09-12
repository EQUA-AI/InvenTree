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
