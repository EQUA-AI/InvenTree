/** Label the actual display zone; a supplied plant setting takes precedence. */
export function observedTime(
  value: string,
  plantTimezone?: string
): string | null {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  let zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (plantTimezone) {
    try {
      new Intl.DateTimeFormat(undefined, { timeZone: plantTimezone });
      zone = plantTimezone;
    } catch {
      // An invalid configuration must not be labelled as plant time.
    }
  }
  return `${date.toLocaleString(undefined, { timeZone: zone })} (${zone})`;
}
