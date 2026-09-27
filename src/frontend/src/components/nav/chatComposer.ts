/**
 * Pure composer behaviour for the AI chat drawer (plan U1).
 *
 * Kept free of React so the Enter/IME guard, scroll-follow policy, page
 * context labelling and empty-state suggestion selection can be unit tested
 * in the Node-only vitest setup. Rendering/translation stays in the drawer.
 */

/** Minimal key-event shape: React.KeyboardEvent is assignable to it. */
export interface EnterKeyState {
  key: string;
  shiftKey: boolean;
  /** True while an IME composition session is active. */
  isComposing?: boolean;
  /** Legacy composition signal (browsers report 229 during composition). */
  keyCode?: number;
}

/**
 * Enter sends, Shift+Enter inserts a newline, and Enter during IME
 * composition never sends — the composition must finish first.
 */
export function shouldSendOnEnter(event: EnterKeyState): boolean {
  if (event.key !== 'Enter' || event.shiftKey) return false;
  if (event.isComposing) return false;
  if (event.keyCode === 229) return false;
  return true;
}

export interface ViewportMetrics {
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
}

/**
 * A reader "at the bottom" within `threshold` pixels. A transcript that does
 * not overflow is always at the bottom.
 */
export function isNearBottom(
  viewport: ViewportMetrics,
  threshold = 80
): boolean {
  const distance =
    viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
  return distance <= threshold;
}

/**
 * New content pulls the viewport only when the reader was already following
 * (near the bottom) or the thread changed. Prepended history is anchored by
 * its own restore logic and must never jump to the bottom.
 */
export function shouldFollowNewContent(state: {
  threadChanged: boolean;
  anchoredPrepend: boolean;
  nearBottom: boolean;
}): boolean {
  if (state.threadChanged) return true;
  if (state.anchoredPrepend) return false;
  return state.nearBottom;
}

/** Friendly page kinds for the drawer's context line (never the raw path). */
export type PageContextKind =
  | 'home'
  | 'machines'
  | 'maintenance'
  | 'parts'
  | 'stock'
  | 'orders'
  | 'other';

export function pageContextKind(pathname: string): PageContextKind {
  const path = pathname.toLowerCase();
  if (path === '/' || path.startsWith('/home')) return 'home';
  if (path.startsWith('/machine/') || path.startsWith('/machines'))
    return 'machines';
  if (path.startsWith('/maintenance')) return 'maintenance';
  if (path.startsWith('/part')) return 'parts';
  if (path.startsWith('/stock')) return 'stock';
  if (path.startsWith('/order')) return 'orders';
  return 'other';
}

/** Which empty-state prompts to offer; labels/messages are translated in the UI. */
export type SuggestionKind =
  | 'machine-status'
  | 'machine-maintenance'
  | 'search-parts'
  | 'create-order'
  | 'low-stock';

export function suggestionKinds(machineName?: string): SuggestionKind[] {
  if (machineName) {
    return ['machine-status', 'machine-maintenance', 'search-parts'];
  }
  return ['search-parts', 'create-order', 'low-stock'];
}
