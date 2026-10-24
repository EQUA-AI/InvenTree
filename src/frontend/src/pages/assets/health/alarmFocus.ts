/** A notification identifies an anomaly by its immutable numeric identifier. */
export function alarmFocusId(value: string | null): string | undefined {
  return value && /^[1-9]\d*$/.test(value)
    ? `machine-alarm-${value}`
    : undefined;
}

export function focusAlarm(value: string | null) {
  const id = alarmFocusId(value);
  const target = id ? document.getElementById(id) : null;
  if (!target) return false;
  target.scrollIntoView({ block: 'center', behavior: 'auto' });
  target.focus({ preventScroll: true });
  return true;
}
