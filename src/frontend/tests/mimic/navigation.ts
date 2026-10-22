/** Reuse an owned Vite harness without starting the dedicated server. */
export function mimicEntry(path = '/') {
  const entry = process.env.AIMMS_MIMIC_ENTRY;
  if (!entry) return path;
  const target = new URL(entry, 'http://fixture.invalid');
  const requested = new URL(path, 'http://fixture.invalid');
  for (const [key, value] of requested.searchParams) {
    target.searchParams.set(key, value);
  }
  target.hash = requested.hash;
  return `${target.pathname}${target.search}${target.hash}`;
}
