/** Owner-selected experimental classifier; zero/missing configuration is off. */
export function phoneRoutingEnabled(limit: unknown): limit is number {
  return (
    typeof limit === 'number' &&
    Number.isInteger(limit) &&
    limit >= 320 &&
    limit <= 600
  );
}

export function classifyVoicePhone(input: {
  limit: unknown;
  screenWidth: number;
  screenHeight: number;
  touchPoints: number;
  coarsePointer: boolean;
}) {
  return (
    phoneRoutingEnabled(input.limit) &&
    input.touchPoints > 0 &&
    input.coarsePointer &&
    Math.min(input.screenWidth, input.screenHeight) > 0 &&
    Math.min(input.screenWidth, input.screenHeight) <= input.limit
  );
}

export function stickyPhoneClassifier(
  read: () => Parameters<typeof classifyVoicePhone>[0]
) {
  let phone: boolean | undefined;
  return () => {
    phone ??= classifyVoicePhone(read());
    return phone;
  };
}

/** One classification for the page, never recomputed on keyboard/rotation resize. */
export const isVoicePhone = stickyPhoneClassifier(() => ({
  limit: window.INVENTREE_SETTINGS?.voice_phone_short_edge_px,
  screenWidth: window.screen.width,
  screenHeight: window.screen.height,
  touchPoints: navigator.maxTouchPoints,
  coarsePointer: window.matchMedia('(pointer: coarse)').matches
}));
