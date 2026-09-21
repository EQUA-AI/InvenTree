export const VOICE_SHORTCUT = {
  binding: 'mod+shift+v',
  aria: 'Control+Shift+V Meta+Shift+V',
  label: 'Ctrl/⌘+Shift+V'
} as const;

export function isEditingTarget(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    Boolean(
      target.closest(
        'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [data-barcode-input]'
      )
    )
  );
}
export function isVoiceShortcut(event: KeyboardEvent) {
  return (
    !event.repeat &&
    !event.altKey &&
    (event.ctrlKey || event.metaKey) &&
    event.shiftKey &&
    event.key.toLowerCase() === 'v' &&
    !isEditingTarget(event.target)
  );
}
export function voiceEscape(
  event: KeyboardEvent,
  playing: boolean
): 'stop' | 'minimize' | null {
  if (event.key !== 'Escape' || event.repeat || isEditingTarget(event.target))
    return null;
  if (
    !(event.target instanceof Element) ||
    !event.target.closest('[data-voice-surface]')
  )
    return null;
  return playing ? 'stop' : 'minimize';
}
