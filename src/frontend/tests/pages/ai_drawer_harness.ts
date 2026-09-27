/**
 * Shared entry helper for the mocked AI chat drawer fixture page
 * (`playwright/aichat-drawer.html`) — real drawer components, mocked auth,
 * route-mocked API. Used by pui_ai_chat_drawer_layout.spec.ts and the
 * drawer-driven scenarios of pui_voice_decisions.spec.ts.
 */
import type { Page, Route } from '@playwright/test';

import {
  type FoundationObservations,
  type FoundationOptions,
  mockChatFoundation,
  openChat
} from './aichat_harness.js';
import {
  type VoiceHarnessOptions,
  type VoiceObservations,
  installVoiceMocks
} from './voice_harness.js';

export { openChat };

export type DrawerTab = 'chat' | 'approvals' | 'history' | 'mail';

export interface DrawerFixtureOptions {
  foundation?: FoundationOptions;
  /** Voice mocks; false installs none. */
  voice?: VoiceHarnessOptions | false;
  /** Persisted drawer tab seeded before load (Mantine useLocalStorage JSON). */
  tab?: DrawerTab;
}

export interface DrawerFixture {
  foundation: FoundationObservations;
  voice: VoiceObservations | null;
}

/**
 * Install route mocks for the fixture page. Register any extra mocks AFTER
 * this call — Playwright applies the most recently registered route first.
 */
export async function prepareDrawerFixture(
  page: Page,
  options: DrawerFixtureOptions = {}
): Promise<DrawerFixture> {
  // Catch-all FIRST so every specific mock below (registered later) wins.
  await page.route('**/api/**', async (route: Route) => {
    await route.fulfill({ json: {} });
  });
  const foundation = await mockChatFoundation(page, options.foundation ?? {});
  const voice =
    options.voice === false
      ? null
      : await installVoiceMocks(page, options.voice ?? {});
  // The Mail tab mounts the real MailboxPanel; answer its list endpoints
  // with valid empty shapes (a bare {} would crash `.results.find`).
  await page.route('**/api/aichat/email/**', async (route: Route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: { results: [] } });
      return;
    }
    await route.fulfill({ json: {} });
  });
  if (options.tab) {
    await page.addInitScript((tab: string) => {
      window.localStorage.setItem(
        'ai-chat-drawer-active-tab',
        JSON.stringify(tab)
      );
    }, options.tab);
  }
  return { foundation, voice };
}

/** Navigate to the fixture (after mocks) and optionally open the drawer. */
export async function loadDrawerFixture(
  page: Page,
  options: { open?: boolean; route?: string } = {}
) {
  const query = options.route
    ? `?route=${encodeURIComponent(options.route)}`
    : '';
  await page.goto(`/playwright/aichat-drawer.html${query}`);
  if (options.open ?? true) await openChat(page);
}
