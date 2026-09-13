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
