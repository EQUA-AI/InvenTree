/** Enum-only ICE diagnostics. Never retain candidate addresses, SDP or credentials. */
export interface VoiceNetworkDiagnostics {
  localCandidateType: 'host' | 'srflx' | 'prflx' | 'relay' | 'unknown';
  remoteCandidateType: 'host' | 'srflx' | 'prflx' | 'relay' | 'unknown';
  protocol: 'udp' | 'tcp' | 'unknown';
}
function candidateType(
  value: unknown
): VoiceNetworkDiagnostics['localCandidateType'] {
  return value === 'host' ||
    value === 'srflx' ||
    value === 'prflx' ||
    value === 'relay'
    ? value
    : 'unknown';
}
export function selectedCandidateDiagnostics(
  stats: RTCStatsReport
): VoiceNetworkDiagnostics | null {
  let pair: RTCIceCandidatePairStats | undefined;
  stats.forEach((entry) => {
    if (entry.type === 'transport' && entry.selectedCandidatePairId) {
      pair = stats.get(entry.selectedCandidatePairId);
    }
  });
  if (!pair || pair.state !== 'succeeded') return null;
  const local = stats.get(pair.localCandidateId);
  const remote = stats.get(pair.remoteCandidateId);
  return {
    localCandidateType: candidateType(local?.candidateType),
    remoteCandidateType: candidateType(remote?.candidateType),
    protocol:
      local?.protocol === 'udp' || local?.protocol === 'tcp'
        ? local.protocol
        : 'unknown'
  };
}
