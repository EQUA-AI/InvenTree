import { t } from '@lingui/core/macro';
export function ageDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return s >= 86400
    ? `${Math.floor(s / 86400)} d`
    : s >= 3600
      ? `${Math.floor(s / 3600)} h`
      : s >= 60
        ? `${Math.floor(s / 60)} min`
        : `${s} s`;
}
export function relativeAge(
  seconds: number | null,
  preferJustNow = false,
  stale = false,
  future = false
): string {
  if (seconds == null || !Number.isFinite(seconds)) return t`age unavailable`;
  const age = ageDuration(seconds);
  return future
    ? t`${age} ahead of your clock`
    : preferJustNow && !stale && seconds >= 0 && seconds < 60
      ? t`just now`
      : t`${age} old`;
}
