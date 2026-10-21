import dayjs from 'dayjs';

/** The API supplies no plant zone. An absent browser zone stays unavailable. */
export function displayClockLabel(at = Date.now()): string {
  let zone: string | undefined;
  try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    zone = undefined;
  }
  return zone ? `${zone}, UTC${dayjs(at).format('Z')}` : 'zone unavailable';
}
export function displayInstant(
  value: string | number | null | undefined,
  options: { offsetIfDiffers?: boolean; pageAt?: number } = {
    offsetIfDiffers: true
  }
): string | null {
  if (
    value == null ||
    value === '' ||
    (typeof value === 'number' && (!Number.isFinite(value) || value <= 0))
  )
    return null;
  const date = dayjs(value);
  if (!date.isValid()) return null;
  const offset =
    options.offsetIfDiffers &&
    date.utcOffset() !== dayjs(options.pageAt ?? Date.now()).utcOffset()
      ? ` UTC${date.format('Z')}`
      : '';
  return date.format('D MMM YYYY HH:mm:ss') + offset;
}
