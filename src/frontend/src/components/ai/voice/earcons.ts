export type Earcon = 'ready' | 'heard' | 'decision' | 'ended';
export interface CueConditions {
  enabled: boolean;
  muted: boolean;
  hidden: boolean;
  playing: boolean;
  sr: boolean;
}
export function cueAllowed(c: CueConditions) {
  return c.enabled && !c.muted && !c.hidden && !c.playing && !c.sr;
}
/** Four deliberately quiet, provisional 120 ms cues; not speech or recordings. */
export class Earcons {
  private context: AudioContext | null = null;
  private last = Number.NEGATIVE_INFINITY;
  unlock() {
    if (typeof AudioContext === 'undefined') return;
    this.context ??= new AudioContext();
    void this.context.resume().catch(() => {});
  }
  play(kind: Earcon, conditions: CueConditions) {
    if (!cueAllowed(conditions) || Date.now() - this.last < 700) return false;
    if (!this.context || this.context.state !== 'running') return false;
    this.last = Date.now();
    const oscillator = this.context.createOscillator();
    const gain = this.context.createGain();
    const now = this.context.currentTime;
    oscillator.frequency.value = {
      ready: 660,
      heard: 880,
      decision: 440,
      ended: 330
    }[kind];
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(0.25, now + 0.01);
    gain.gain.linearRampToValueAtTime(0, now + 0.12);
    oscillator.connect(gain);
    gain.connect(this.context.destination);
    oscillator.start(now);
    oscillator.stop(now + 0.12);
    oscillator.onended = () => {
      oscillator.disconnect();
      gain.disconnect();
    };
    return true;
  }
  close() {
    void this.context?.close();
    this.context = null;
  }
}
