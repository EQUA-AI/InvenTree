import dayjs from 'dayjs';

/** No plant timezone is currently supplied by Health or Performance. */
export function displayClockLabel(at = Date.now()): string {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  return `${zone}, UTC${dayjs(at).format('Z')}`;
}

/** All full instants use the same 24-hour browser clock; invalid is unavailable. */
export function displayInstant(
  value: string | number | null | undefined
): string | null {
  if (value == null || value === '') return null;
  const date = dayjs(value);
  return date.isValid() ? date.format('D MMM YYYY HH:mm:ss') : null;
}
