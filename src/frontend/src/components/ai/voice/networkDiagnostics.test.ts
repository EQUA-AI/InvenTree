import { describe, expect, it } from 'vitest';
import { selectedCandidateDiagnostics } from './networkDiagnostics';

describe('privacy-safe candidate pair evidence', () => {
  it('keeps only allow-listed enums from the selected successful pair', () => {
    const stats = new Map([
      ['transport', { type: 'transport', selectedCandidatePairId: 'pair' }],
      [
        'pair',
        {
          type: 'candidate-pair',
          state: 'succeeded',
          localCandidateId: 'local',
          remoteCandidateId: 'remote'
        }
      ],
      [
        'local',
        {
          candidateType: 'srflx',
          protocol: 'udp',
          address: '192.0.2.5',
          usernameFragment: 'private'
        }
      ],
      [
        'remote',
        {
          candidateType: 'host',
          address: '192.0.2.6',
          url: 'turn:private.example'
        }
      ]
    ]) as unknown as RTCStatsReport;
    expect(selectedCandidateDiagnostics(stats)).toEqual({
      localCandidateType: 'srflx',
      remoteCandidateType: 'host',
      protocol: 'udp'
    });
    expect(JSON.stringify(selectedCandidateDiagnostics(stats))).not.toContain(
      '192.0.2'
    );
  });
  it('does not infer connectivity from arbitrary candidate rows', () => {
    expect(
      selectedCandidateDiagnostics(new Map() as unknown as RTCStatsReport)
    ).toBeNull();
  });
});

describe('bounded numeric transport measurements', () => {
  // Sanitized shape from a recorded stats poll: real row kinds and fields,
  // synthetic values. Candidate rows still carry address/url junk that must
  // never leak into the diagnostics.
  function recordingStats(
    pairExtra: Record<string, unknown> = {},
    audioExtra: Record<string, unknown> = {},
    extraRows: [string, Record<string, unknown>][] = []
  ) {
    return new Map([
      ['transport', { type: 'transport', selectedCandidatePairId: 'pair' }],
      [
        'pair',
        {
          type: 'candidate-pair',
          state: 'succeeded',
          localCandidateId: 'local',
          remoteCandidateId: 'remote',
          currentRoundTripTime: 0.042,
          ...pairExtra
        }
      ],
      [
        'local',
        {
          candidateType: 'srflx',
          protocol: 'udp',
          address: '192.0.2.5',
          port: 40000,
          usernameFragment: 'private'
        }
      ],
      [
        'remote',
        {
          candidateType: 'host',
          address: '192.0.2.6',
          port: 40001,
          url: 'turn:private.example'
        }
      ],
      [
        'inbound-audio',
        {
          type: 'inbound-rtp',
          kind: 'audio',
          ssrc: 12345,
          jitter: 0.003,
          packetsReceived: 990,
          packetsLost: 10,
          ...audioExtra
        }
      ],
      ...extraRows
    ]) as unknown as RTCStatsReport;
  }
  it('measures bounded RTT, jitter and loss from the selected pair and its inbound audio', () => {
    const diagnostics = selectedCandidateDiagnostics(recordingStats());
    expect(diagnostics).toMatchObject({
      localCandidateType: 'srflx',
      remoteCandidateType: 'host',
      protocol: 'udp',
      roundTripTimeMs: 42,
      jitterMs: 3
    });
    expect(diagnostics?.packetLossRatio).toBeCloseTo(0.01, 5);
    const dumped = JSON.stringify(diagnostics);
    expect(dumped).not.toContain('192.0.2');
    expect(dumped).not.toContain('private');
    expect(dumped).not.toContain('turn:');
    expect(dumped).not.toContain('4000');
    expect(dumped).not.toContain('12345');
  });
  it('keeps negative, out-of-range and non-finite measurements absent', () => {
    expect(
      selectedCandidateDiagnostics(
        recordingStats({ currentRoundTripTime: -0.5 }, { jitter: -0.001 })
      )
    ).toMatchObject({ protocol: 'udp' });
    expect(
      selectedCandidateDiagnostics(
        recordingStats({ currentRoundTripTime: -0.5 }, { jitter: -0.001 })
      )?.roundTripTimeMs
    ).toBeUndefined();
    expect(
      selectedCandidateDiagnostics(
        recordingStats({ currentRoundTripTime: -0.5 }, { jitter: -0.001 })
      )?.jitterMs
    ).toBeUndefined();
    expect(
      selectedCandidateDiagnostics(
        recordingStats({ currentRoundTripTime: 120 }, { jitter: 0.003 })
      )?.roundTripTimeMs
    ).toBeUndefined();
    expect(
      selectedCandidateDiagnostics(
        recordingStats({ currentRoundTripTime: Number.NaN })
      )?.roundTripTimeMs
    ).toBeUndefined();
    expect(
      selectedCandidateDiagnostics(
        recordingStats({}, { jitter: Number.POSITIVE_INFINITY })
      )?.jitterMs
    ).toBeUndefined();
    // Loss ratio is a ratio: a malformed counter set cannot exceed 1.
    expect(
      selectedCandidateDiagnostics(
        recordingStats({}, { packetsReceived: -50, packetsLost: -50 })
      )?.packetLossRatio
    ).toBeUndefined();
    expect(
      selectedCandidateDiagnostics(
        recordingStats({}, { packetsReceived: 0, packetsLost: 0 })
      )?.packetLossRatio
    ).toBeUndefined();
  });
  it('leaves audio measurements unknown when the inbound audio source cannot be bound', () => {
    // Two inbound audio rows: no honest way to pick the selected source.
    const ambiguous = recordingStats({}, {}, [
      [
        'inbound-audio-2',
        {
          type: 'inbound-rtp',
          kind: 'audio',
          jitter: 0.009,
          packetsReceived: 1,
          packetsLost: 0
        }
      ]
    ]);
    const diagnostics = selectedCandidateDiagnostics(ambiguous);
    expect(diagnostics?.jitterMs).toBeUndefined();
    expect(diagnostics?.packetLossRatio).toBeUndefined();
    // The selected pair's own RTT is still measured.
    expect(diagnostics?.roundTripTimeMs).toBe(42);
    // Unrelated rows (video) are never used for audio measurements.
    const videoOnly = new Map(recordingStats()) as unknown as Map<
      string,
      Record<string, unknown>
    >;
    videoOnly.delete('inbound-audio');
    videoOnly.set('inbound-video', {
      type: 'inbound-rtp',
      kind: 'video',
      jitter: 0.004,
      packetsReceived: 10,
      packetsLost: 1
    });
    const videoDiagnostics = selectedCandidateDiagnostics(
      videoOnly as unknown as RTCStatsReport
    );
    expect(videoDiagnostics?.jitterMs).toBeUndefined();
    expect(videoDiagnostics?.packetLossRatio).toBeUndefined();
    expect(videoDiagnostics?.roundTripTimeMs).toBe(42);
  });
});
