/** In-memory RMS only: no recording, audio retention or biometric processing. */
export class SilenceCheck {
  private since: number | null = null;
  constructor(
    private floor: number,
    private windowMs: number
  ) {}
  sample(rms: number, active: boolean, now: number): boolean {
    if (!active || rms >= this.floor) {
      this.since = null;
      return false;
    }
    this.since ??= now;
    return now - this.since >= this.windowMs;
  }
}
export function outputDisappeared(
  before: MediaDeviceInfo[],
  after: MediaDeviceInfo[]
): boolean {
  const old = before.filter(
    (d) =>
      d.kind === 'audiooutput' &&
      d.deviceId &&
      !['default', 'communications'].includes(d.deviceId)
  );
  // Browsers that never enumerate outputs cannot claim a route disappeared.
  return (
    old.length > 0 &&
    old.some(
      (d) =>
        !after.some(
          (n) => n.kind === 'audiooutput' && n.deviceId === d.deviceId
        )
    )
  );
}
export function monitorMicrophone(
  stream: MediaStream,
  floor: number,
  windowMs: number,
  active: () => boolean,
  warn: () => void
) {
  if (typeof AudioContext === 'undefined') return () => {};
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 256;
  source.connect(analyser); // Deliberately not connected to speakers.
  const samples = new Float32Array(analyser.fftSize);
  const check = new SilenceCheck(floor, windowMs);
  let warned = false;
  const timer = window.setInterval(() => {
    analyser.getFloatTimeDomainData(samples);
    const rms = Math.sqrt(
      samples.reduce((sum, v) => sum + v * v, 0) / samples.length
    );
    if (!warned && check.sample(rms, active(), Date.now())) {
      warned = true;
      warn();
    }
  }, 100);
  return () => {
    window.clearInterval(timer);
    source.disconnect();
    void context.close();
  };
}
