import { describe, expect, it } from 'vitest';
import { VoiceTiming } from './voiceTiming';

const spoken = {
  utterance_id: '11111111-1111-1111-1111-111111111111',
  spoken_summary: 'PRIVATE_SENTINEL',
  spoken_summary_hash: 'a'.repeat(64),
  playback_state: 'requested'
};
function fixture() {
  let clock = 0;
  const reports: unknown[] = [];
  const timing = new VoiceTiming(
    (report) => reports.push(report),
    () => clock,
    () => 1700000000000 + clock
  );
  return {
    timing,
    reports,
    at: (value: number) => {
      clock = value;
    }
  };
}
function response(timing: VoiceTiming, id = 'response') {
  timing.event({ type: 'response.created', response: { id } });
  timing.event({ type: 'response.audio.delta', response_id: id });
  timing.event({
    type: 'response.audio_transcript.done',
    response_id: id,
    transcript: spoken.spoken_summary
  });
}

// Sanitized shape/order from the closed provider campaign; deliberately absent
// buffer IDs and metadata. Synthetic text, IDs and energy are unit-test only.
function providerStart(timing: VoiceTiming, id: string) {
  timing.event({ type: 'response.created', response: { id } });
  timing.event({
    type: 'response.audio_transcript.delta',
    response_id: id,
    delta: 'synthetic'
  });
  timing.event({ type: 'output_audio_buffer.started' });
}
function providerEnd(timing: VoiceTiming, id: string, transcript: string) {
  timing.event({
    type: 'response.audio_transcript.done',
    response_id: id,
    transcript
  });
  timing.event({ type: 'response.audio.done', response_id: id });
  timing.event({ type: 'output_audio_buffer.stopped' });
  timing.event({
    type: 'response.done',
    response: { id, status: 'completed' }
  });
}
describe('optional voice timing', () => {
  it('rejects a stats request completed after Stop or a new turn', () => {
    const { timing, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    const context = timing.samplingContext;
    timing.begin();
    timing.sampleEnergy(0);
    providerStart(timing, 'new');
    timing.bind(spoken);
    timing.sampleEnergy(1, context);
    providerEnd(timing, 'new', spoken.spoken_summary);
    expect(reports).toEqual([]);
  });
  it('measures final speech after a completed interim and a clean RTP boundary', () => {
    const { timing, at, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    timing.sampleEnergy(0);
    providerStart(timing, 'interim');
    at(10);
    timing.sampleEnergy(1);
    providerEnd(timing, 'interim', 'One moment.');
    at(20);
    timing.sampleEnergy(1); // Actual quiet interval, not response.done.
    providerStart(timing, 'final');
    at(150);
    timing.sampleEnergy(2);
    at(180);
    providerEnd(timing, 'final', spoken.spoken_summary);
    at(200);
    timing.bind(spoken); // Binding may arrive after playback ended.
    expect(reports).toHaveLength(1);
    expect(reports[0]).toMatchObject({
      first_playback_epoch_ms: 1700000000150,
      timing: { submit_to_observed_playback_ms: 150 }
    });
  });
  it('also measures when the final binding arrives before its lifecycle', () => {
    const { timing, at, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    timing.sampleEnergy(0);
    timing.bind(spoken);
    providerStart(timing, 'final');
    at(40);
    timing.sampleEnergy(1);
    providerEnd(timing, 'final', spoken.spoken_summary);
    expect(reports).toHaveLength(1);
  });
  it.each([
    'crossing',
    'completion-only',
    'duplicate-text',
    'missing-transcript',
    'overlap'
  ])('leaves ambiguous %s interim/final sequences unmeasured', (kind) => {
    const { timing, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    timing.sampleEnergy(0);
    providerStart(timing, 'interim');
    timing.sampleEnergy(1);
    if (kind === 'completion-only')
      timing.event({
        type: 'response.done',
        response: { id: 'interim', status: 'completed' }
      });
    else if (kind !== 'overlap') {
      providerEnd(
        timing,
        'interim',
        kind === 'duplicate-text' ? spoken.spoken_summary : 'One moment.'
      );
      if (kind === 'missing-transcript') {
        // A separate retired output with no final text remains unknowable.
        timing.sampleEnergy(1);
        providerStart(timing, 'unknown');
        timing.event({ type: 'output_audio_buffer.stopped' });
      }
    }
    if (kind !== 'crossing') timing.sampleEnergy(1);
    providerStart(timing, 'final');
    timing.sampleEnergy(2);
    providerEnd(timing, 'final', spoken.spoken_summary);
    timing.bind(spoken);
    expect(reports).toEqual([]);
  });
  it.each([
    'no-energy',
    'initial-positive',
    'counter-reset',
    'metadata-mismatch',
    'canceled'
  ])('does not report %s as observed playback', (kind) => {
    const { timing, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    if (kind !== 'initial-positive')
      timing.sampleEnergy(kind === 'counter-reset' ? 5 : 0);
    timing.event({
      type: 'response.created',
      response: {
        id: 'final',
        ...(kind === 'metadata-mismatch'
          ? {
              metadata: {
                aimms_utterance_id: '22222222-2222-2222-2222-222222222222',
                aimms_spoken_hash: 'b'.repeat(64)
              }
            }
          : {})
      }
    });
    timing.event({ type: 'output_audio_buffer.started' });
    timing.sampleEnergy(kind === 'no-energy' ? 0 : 1);
    providerEnd(timing, 'final', spoken.spoken_summary);
    if (kind === 'canceled')
      timing.event({
        type: 'response.done',
        response: { id: 'final', status: 'cancelled' }
      });
    timing.bind(spoken);
    expect(reports).toEqual([]);
  });
  it('does not let late bound interim events steal a final window', () => {
    const { timing, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    timing.sampleEnergy(0);
    providerStart(timing, 'interim');
    timing.sampleEnergy(1);
    providerEnd(timing, 'interim', 'One moment.');
    timing.sampleEnergy(1);
    providerStart(timing, 'final');
    timing.event({
      type: 'output_audio_buffer.stopped',
      response_id: 'interim'
    });
    timing.sampleEnergy(2);
    providerEnd(timing, 'final', spoken.spoken_summary);
    timing.bind(spoken);
    expect(reports).toHaveLength(1);
  });
  it('drops previous epoch/hidden/stopped output without exporting diagnostic content', () => {
    const reasons: unknown[] = [];
    const reports: unknown[] = [];
    const timing = new VoiceTiming(
      (r) => reports.push(r),
      () => 0,
      () => 1700000000000,
      (r) => reasons.push(r)
    );
    timing.reset('a'.repeat(32));
    timing.begin();
    timing.sampleEnergy(0);
    providerStart(timing, 'old');
    timing.sampleEnergy(1);
    timing.stop();
    timing.reset('b'.repeat(32));
    timing.begin();
    providerEnd(timing, 'old', spoken.spoken_summary);
    timing.bind(spoken);
    timing.sampleEnergy(2);
    expect(reports).toEqual([]);
    expect(JSON.stringify(reasons)).not.toContain('PRIVATE');
  });
  it('accepts a sole provider-shaped output without a response-bound buffer ID', () => {
    const { timing, at, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    providerStart(timing, 'final');
    at(200);
    timing.energy();
    providerEnd(timing, 'final', spoken.spoken_summary);
    timing.bind(spoken);
    expect(reports).toHaveLength(1);
    expect(reports[0]).toMatchObject({
      first_playback_epoch_ms: 1700000000200
    });
  });
  it('drops invalid opaque bindings instead of exporting arbitrary server text', () => {
    const { timing, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    response(timing);
    timing.bind({ ...spoken, utterance_id: 'PRIVATE_SENTINEL' });
    timing.energy();
    expect(reports).toEqual([]);
    timing.reset('PRIVATE_SENTINEL');
    expect(timing.begin()).toBeUndefined();
  });
  it('is dark without an epoch and preserves missing measurements', () => {
    const { timing, reports } = fixture();
    expect(timing.begin('item')).toBeUndefined();
    response(timing);
    timing.bind(spoken);
    timing.energy();
    expect(reports).toEqual([]);
    timing.reset('a'.repeat(32));
    expect(timing.begin('missing')?.speech_to_final_ms).toBeUndefined();
  });
  it('uses one monotonic clock and exports only numeric intervals and opaque bindings', () => {
    const { timing, at, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.event({
      type: 'input_audio_buffer.speech_stopped',
      item_id: 'item'
    });
    at(100);
    timing.event({
      type: 'conversation.item.input_audio_transcription.completed',
      item_id: 'item',
      transcript: 'PRIVATE_SENTINEL'
    });
    at(120);
    timing.acknowledged('item');
    at(150);
    expect(timing.begin('item')).toEqual({
      speech_to_final_ms: 100,
      final_to_submit_ms: 50,
      ack_schedule_ms: 120
    });
    response(timing);
    at(200);
    timing.energy();
    timing.bind(spoken);
    timing.energy();
    timing.bind(spoken);
    expect(reports).toHaveLength(1);
    expect(reports[0]).toMatchObject({
      provenance: 'rtp_energy_proxy',
      first_playback_epoch_ms: 1700000000200,
      timing: { submit_to_observed_playback_ms: 50 }
    });
    expect(JSON.stringify(reports)).not.toContain('PRIVATE_SENTINEL');
  });
  it('never interprets provider completion as first playback', () => {
    const { timing, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    response(timing);
    timing.bind(spoken);
    timing.event({
      type: 'response.done',
      response: { id: 'response', status: 'completed' }
    });
    expect(reports).toEqual([]);
  });
  it('refuses ambiguous, mismatched and stale-generation observations', () => {
    const { timing, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    response(timing);
    response(timing, 'second');
    timing.bind(spoken);
    timing.energy();
    expect(reports).toEqual([]);
    timing.begin();
    response(timing);
    timing.bind({ ...spoken, spoken_summary: 'different' });
    timing.energy();
    expect(reports).toEqual([]);
    timing.reset('b'.repeat(32));
    timing.bind(spoken);
    timing.energy();
    expect(reports).toEqual([]);
  });
  it('records local pause without fabricating audio onset or retaining stopped output', () => {
    const { timing, reports, at } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    timing.bind(spoken);
    at(10);
    timing.localStop(8);
    timing.energy();
    expect(reports).toHaveLength(1);
    expect(reports[0]).toMatchObject({
      provenance: 'local_pause_proxy',
      timing: { local_stop_ms: 2 }
    });
    expect(reports[0]).not.toHaveProperty('first_playback_epoch_ms');
  });
  it('keeps report failure outside the business path', () => {
    const timing = new VoiceTiming(() => {
      throw Error('transport failed');
    });
    timing.reset('a'.repeat(32));
    timing.begin();
    response(timing);
    timing.bind(spoken);
    expect(() => timing.energy()).not.toThrow();
  });
});

// Package E: first feedback onset vs useful-answer onset. Filler is observed
// ONLY from actual provider metadata (`aimms_onset_kind: 'filler'` with its
// opaque utterance id/hash) plus a clean RTP boundary — never inferred from a
// status transcript label. The answer bind stays the completed persisted
// spoken summary/hash. `generation` is the LOCAL turn generation; provider
// `aimms_dispatch_generation` metadata is deliberately never read or mixed in.
describe('feedback and answer onset', () => {
  const fillerMeta = {
    aimms_onset_kind: 'filler',
    aimms_utterance_id: '33333333-3333-3333-3333-333333333333',
    aimms_spoken_hash: 'c'.repeat(64)
  };
  function fillerStart(timing: VoiceTiming, id: string) {
    timing.event({
      type: 'response.created',
      response: { id, metadata: fillerMeta }
    });
    timing.event({ type: 'response.audio.delta', response_id: id });
    timing.event({ type: 'output_audio_buffer.started' });
  }
  it('bounds the queue wait interval just before, at and after the threshold', () => {
    const { timing, at } = fixture();
    timing.reset('a'.repeat(32));
    at(299_999);
    expect(timing.begin('item', 0)?.ms_queue).toBe(299_999);
    timing.reset('a'.repeat(32));
    at(300_000);
    expect(timing.begin('item', 0)?.ms_queue).toBe(300_000);
    timing.reset('a'.repeat(32));
    at(300_001);
    expect(timing.begin('item', 0)?.ms_queue).toBeUndefined();
    at(0);
    expect(timing.begin('item', 100)?.ms_queue).toBeUndefined();
  });
  it('reports a fast answer as its own first feedback', () => {
    const { timing, at, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    timing.sampleEnergy(0);
    timing.bind(spoken);
    providerStart(timing, 'final');
    at(30);
    timing.sampleEnergy(1);
    providerEnd(timing, 'final', spoken.spoken_summary);
    expect(reports).toHaveLength(1);
    expect(reports[0]).toMatchObject({
      onset_kind: 'answer',
      generation: 1,
      first_playback_epoch_ms: 1700000000030,
      timing: { feedback_onset_ms: 30, answer_onset_ms: 30 }
    });
  });
  it('keeps a metadata-confirmed filler onset separate from the answer onset', () => {
    const { timing, at, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    timing.sampleEnergy(0);
    fillerStart(timing, 'cue');
    at(10);
    timing.sampleEnergy(1);
    providerEnd(timing, 'cue', 'One moment.');
    at(20);
    timing.sampleEnergy(1); // Clean quiet interval before the answer.
    providerStart(timing, 'final');
    at(150);
    timing.sampleEnergy(2);
    providerEnd(timing, 'final', spoken.spoken_summary);
    timing.bind(spoken);
    expect(reports).toHaveLength(1);
    expect(reports[0]).toMatchObject({
      // Real server contract: the report row is the bound ANSWER row, so its
      // classification must match its own observed onset ('answer' at its own
      // wall). The earlier filler onset survives only as the distinct
      // first-feedback interval, never as this row's onset.
      onset_kind: 'answer',
      first_playback_epoch_ms: 1700000000150,
      timing: { feedback_onset_ms: 10, answer_onset_ms: 150 }
    });
  });
  it('never lets a filler onset populate answer_onset_ms', () => {
    const { timing, at, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    timing.sampleEnergy(0);
    fillerStart(timing, 'cue');
    at(10);
    timing.sampleEnergy(1);
    providerEnd(timing, 'cue', 'One moment.');
    timing.bind(spoken);
    timing.finish();
    expect(reports).toHaveLength(1);
    expect(reports[0]).toMatchObject({
      coverage_reason: 'not_observed',
      timing: { feedback_onset_ms: 10 }
    });
    const timing2 = (reports[0] as { timing: Record<string, unknown> }).timing;
    expect(timing2).not.toHaveProperty('answer_onset_ms');
    expect(timing2.answer_onset_ms).not.toBe(0);
    // Honest unknown coverage: the filler's provider metadata is not a
    // persisted-row binding, so the bound answer row claims NO onset_kind and
    // NO onset timestamp — only the honest first-feedback interval remains.
    expect(reports[0]).not.toHaveProperty('onset_kind');
    expect(reports[0]).not.toHaveProperty('first_playback_epoch_ms');
  });
  it('satisfies real VoiceTimingReport onset honesty on filler-first reports', () => {
    for (const answer of [true, false]) {
      const { timing, at, reports } = fixture();
      timing.reset('a'.repeat(32));
      timing.begin();
      timing.sampleEnergy(0);
      fillerStart(timing, 'cue');
      at(10);
      timing.sampleEnergy(1);
      providerEnd(timing, 'cue', 'One moment.');
      at(20);
      timing.sampleEnergy(1); // Clean quiet interval before any answer.
      if (answer) {
        providerStart(timing, 'final');
        at(150);
        timing.sampleEnergy(2);
        providerEnd(timing, 'final', spoken.spoken_summary);
      }
      timing.bind(spoken);
      if (!answer) timing.finish();
      expect(reports).toHaveLength(1);
      const report = reports[0] as Record<string, unknown>;
      // Real wire rules: onset_kind requires first_playback_epoch_ms, and a
      // coverage_reason only explains a MISSING onset.
      if ('onset_kind' in report)
        expect(report.first_playback_epoch_ms).toBeTypeOf('number');
      if ('coverage_reason' in report)
        expect(report).not.toHaveProperty('first_playback_epoch_ms');
      // The report row is the bound answer row: never classified 'filler'.
      expect(report.utterance_id).toBe(spoken.utterance_id);
      expect(report.onset_kind).not.toBe('filler');
    }
  });
  it('does not infer filler from an unclassified status output', () => {
    const { timing, at, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    timing.sampleEnergy(0);
    providerStart(timing, 'status'); // No aimms_onset_kind metadata.
    at(10);
    timing.sampleEnergy(1);
    providerEnd(timing, 'status', 'One moment.');
    at(20);
    timing.sampleEnergy(1);
    providerStart(timing, 'final');
    at(150);
    timing.sampleEnergy(2);
    providerEnd(timing, 'final', spoken.spoken_summary);
    timing.bind(spoken);
    expect(reports).toHaveLength(1);
    expect(reports[0]).toMatchObject({
      onset_kind: 'answer',
      timing: { feedback_onset_ms: 150, answer_onset_ms: 150 }
    });
  });
  it('keeps missing times absent with content-free coverage', () => {
    const { timing, at, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    timing.sampleEnergy(0);
    timing.bind(spoken);
    providerStart(timing, 'final');
    at(120);
    providerEnd(timing, 'final', spoken.spoken_summary); // Never observed.
    timing.finish();
    expect(reports).toHaveLength(1);
    expect(reports[0]).toMatchObject({
      provenance: 'rtp_energy_proxy',
      coverage_reason: 'no_audio_proxy',
      generation: 1
    });
    expect(reports[0]).not.toHaveProperty('onset_kind');
    expect(reports[0]).not.toHaveProperty('first_playback_epoch_ms');
    const t = (reports[0] as { timing: Record<string, unknown> }).timing;
    expect(t).not.toHaveProperty('answer_onset_ms');
    expect(t).not.toHaveProperty('feedback_onset_ms');
    expect(JSON.stringify(reports)).not.toContain('PRIVATE_SENTINEL');
  });
  it('covers hidden and canceled boundaries without onset claims', () => {
    const hidden = fixture();
    hidden.timing.reset('a'.repeat(32));
    hidden.timing.begin();
    hidden.timing.sampleEnergy(0);
    hidden.timing.bind(spoken);
    hidden.timing.finish('hidden_page');
    expect(hidden.reports).toHaveLength(1);
    expect(hidden.reports[0]).toMatchObject({ coverage_reason: 'hidden_page' });
    expect(hidden.reports[0]).not.toHaveProperty('onset_kind');

    const canceled = fixture();
    canceled.timing.reset('a'.repeat(32));
    canceled.timing.begin();
    canceled.timing.sampleEnergy(0);
    providerStart(canceled.timing, 'final');
    canceled.timing.sampleEnergy(1);
    canceled.timing.event({
      type: 'response.done',
      response: { id: 'final', status: 'cancelled' }
    });
    canceled.timing.bind(spoken);
    canceled.timing.finish();
    expect(canceled.reports).toHaveLength(1);
    expect(canceled.reports[0]).toMatchObject({ coverage_reason: 'canceled' });
    expect(canceled.reports[0]).not.toHaveProperty('onset_kind');
  });
  it('fences late stats and duplicate output across generations', () => {
    const { timing, at, reports } = fixture();
    timing.reset('a'.repeat(32));
    timing.begin();
    timing.sampleEnergy(0);
    timing.bind(spoken);
    timing.finish();
    expect(reports).toHaveLength(1);
    // Late events after the boundary can never add or duplicate a report.
    at(500);
    providerStart(timing, 'final');
    timing.sampleEnergy(1);
    providerEnd(timing, 'final', spoken.spoken_summary);
    timing.energy();
    timing.finish();
    timing.bind(spoken);
    expect(reports).toHaveLength(1);
  });
});
