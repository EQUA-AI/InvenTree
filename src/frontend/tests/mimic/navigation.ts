/** Reuse an owned Vite harness without starting the dedicated server. */
export const mimicEntry = (path = '/') =>
  `${process.env.AIMMS_MIMIC_ENTRY ?? '/'}${path.slice(1)}`;
