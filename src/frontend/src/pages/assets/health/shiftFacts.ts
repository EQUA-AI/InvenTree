/** Signed display offsets are separate from timezone conversion. */
export function shiftFacts(
  seconds: number | null | undefined,
  time?: string | null
) {
  if (seconds == null || !Number.isFinite(seconds) || seconds === 0)
    return {
      direction: 'unknown' as const,
      seconds: null,
      recorded: null,
      sign: ''
    };
  const ms = time ? Date.parse(time) : Number.NaN;
  return {
    direction: seconds < 0 ? ('earlier' as const) : ('later' as const),
    seconds: Math.abs(seconds),
    sign: seconds < 0 ? '−' : '+',
    recorded: Number.isFinite(ms) ? ms - seconds * 1000 : null
  };
}
