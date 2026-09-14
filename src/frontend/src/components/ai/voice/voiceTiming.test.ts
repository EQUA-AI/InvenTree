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
