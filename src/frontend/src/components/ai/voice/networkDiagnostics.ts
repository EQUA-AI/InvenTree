/**
 * Enum-only ICE diagnostics plus bounded numeric transport measurements.
 * Never retain candidate addresses, ports, candidate IDs, URLs, SDP or
 * credentials. Absent/invalid/unrelated stats stay absent — no inference.
 */
export interface VoiceNetworkDiagnostics {
  localCandidateType: 'host' | 'srflx' | 'prflx' | 'relay' | 'unknown';
  remoteCandidateType: 'host' | 'srflx' | 'prflx' | 'relay' | 'unknown';
  protocol: 'udp' | 'tcp' | 'unknown';
  /** Selected candidate pair RTT in ms (bounded 0..60000). */
  roundTripTimeMs?: number;
  /** Inbound audio jitter in ms (bounded 0..5000). */
  jitterMs?: number;
  /** Inbound audio packet loss ratio (bounded 0..1). */
  packetLossRatio?: number;
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
function bounded(value: unknown, max: number): number | undefined {
  return typeof value === 'number' &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= max
    ? value
    : undefined;
}
function secondsToMs(value: unknown, maxMs: number): number | undefined {
  return typeof value === 'number' ? bounded(value * 1000, maxMs) : undefined;
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
  const roundTripTimeMs = secondsToMs(pair.currentRoundTripTime, 60000);
  // Audio measurements come ONLY from the single bindable inbound-audio row.
  // Ambiguous (several audio rows) or unrelated (video) rows leave them
  // honestly unknown — connectivity is never inferred from arbitrary rows.
  let jitterMs: number | undefined;
  let packetLossRatio: number | undefined;
  const inboundAudio: Record<string, unknown>[] = [];
  stats.forEach((entry) => {
    if (entry.type === 'inbound-rtp' && entry.kind === 'audio')
      inboundAudio.push(entry);
  });
  if (inboundAudio.length === 1) {
    const audio = inboundAudio[0];
    jitterMs = secondsToMs(audio.jitter, 5000);
    const received = audio.packetsReceived;
    const lost = audio.packetsLost;
    if (
      typeof received === 'number' &&
      typeof lost === 'number' &&
      Number.isFinite(received) &&
      Number.isFinite(lost) &&
      received >= 0 &&
      lost >= 0 &&
      received + lost > 0
    ) {
      packetLossRatio = bounded(lost / (received + lost), 1);
    }
  }
  return {
    localCandidateType: candidateType(local?.candidateType),
    remoteCandidateType: candidateType(remote?.candidateType),
    protocol:
      local?.protocol === 'udp' || local?.protocol === 'tcp'
        ? local.protocol
        : 'unknown',
    ...(roundTripTimeMs !== undefined ? { roundTripTimeMs } : {}),
    ...(jitterMs !== undefined ? { jitterMs } : {}),
    ...(packetLossRatio !== undefined ? { packetLossRatio } : {})
  };
}
