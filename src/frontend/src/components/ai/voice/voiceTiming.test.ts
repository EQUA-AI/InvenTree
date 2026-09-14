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
describe('optional voice timing', () => {
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
