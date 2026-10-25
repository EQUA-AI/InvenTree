/** C06/T23 draft identity cleanup: mounted callers with wholly mocked auth/API. */
/** C06/T17/T23: real drawer and hooks, wholly mocked auth/API, no credentials. */
import { type Page, expect, test } from '@playwright/test';
import {
  loadDrawerFixture,
  prepareDrawerFixture
} from './ai_drawer_harness.js';
import { goldenEvents, sseBody, threadId } from './aichat_harness.js';

const secondId = 'thread-draft-b';
const stamp = '2026-10-23T00:00:00Z';
const fileId = 'draft-ready-file';
const editor = (page: Page) =>
  page.getByRole('textbox', { name: 'Message', exact: true });
const attachment = (page: Page) =>
  page.getByTestId(`remove-ai-chat-attachment-${fileId}`);

async function setup(page: Page) {
  const fixture = await prepareDrawerFixture(page, {
    voice: false,
    tab: 'chat'
  });
  let deleted = false;
  const chatPosts: string[] = [];
  await page.route('**/api/ai/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && !path.endsWith('/upload')) {
      chatPosts.push(path);
      await route.fulfill({
        contentType: 'text/event-stream',
        body: sseBody(goldenEvents)
      });
      return;
    }
    await route.fallback();
  });
  await page.route('**/api/ai/threads**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'GET' && path.endsWith('/threads')) {
      await route.fulfill({
        json: {
          threads: (deleted ? [secondId] : [threadId, secondId]).map((id) => ({
            thread_id: id,
            title:
              id === threadId ? 'C06 Conversation A' : 'C06 Conversation B',
            created_at: stamp,
            last_activity: stamp,
            is_persisted: true
          })),
          shared_threads: [],
          has_more: false,
          sync_token: null,
          capabilities: { thread_scope: true, thread_focus: true }
        }
      });
      return;
    }
    const id = path.split('/').at(-1);
    if (request.method() === 'DELETE' && id === threadId) {
      deleted = true;
      await route.fulfill({ json: { thread_id: id, status: 'deleted' } });
      return;
    }
    if (request.method() === 'GET' && (id === threadId || id === secondId)) {
      await route.fulfill({
        json: {
          thread_id: id,
          title: id === threadId ? 'C06 Conversation A' : 'C06 Conversation B',
          created_at: stamp,
          updated_at: stamp,
          messages: [
            {
              id: `${id}-answer`,
              role: 'assistant',
              content: id === threadId ? 'C06 Answer A' : 'C06 Answer B',
              timestamp: stamp
            }
          ],
          active_scope: {
            mode: 'legacy_unconfirmed',
            version: 0,
            display_label: ''
          }
        }
      });
      return;
    }
    await route.fallback();
  });
  await page.route('**/api/ai/upload', async (route) => {
    expect(route.request().method()).toBe('POST');
    expect(route.request().postData()).toContain(
      `name="thread_id"\r\n\r\n${threadId}`
    );
    await route.fulfill({
      json: {
        file_id: fileId,
        filename: 'C06-ready.txt',
        content_type: 'text/plain',
        size: 11,
        thread_id: threadId
      }
    });
  });
  await loadDrawerFixture(page);
  await select(page, 'A');
  return {
    fixture,
    chatPosts
  };
}

async function select(page: Page, letter: 'A' | 'B') {
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: `C06 Conversation ${letter}` })
    .click();
  await expect(
    page.getByText(`C06 Answer ${letter}`, { exact: true })
  ).toBeVisible();
  await expect(editor(page)).toBeEnabled();
}

async function attachReady(page: Page) {
  await page.locator('input[type=file]').setInputFiles({
    name: 'C06-ready.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('C06 fixture')
  });
  await expect(attachment(page)).toBeVisible();
}

// Caller-boundary regressions. Application source remains frozen.
const identityKinds = [
  'logout',
  'account',
  'host',
  'profile',
  'same-host'
] as const;

async function prepareIdentity(page: Page) {
  const fixture = await setup(page);
  await page.evaluate(async () => {
    const localUrl = '/src/states/LocalState.tsx';
    const { useLocalState } = await import(localUrl);
    const settings = (window as any).INVENTREE_SETTINGS;
    if (settings) settings.api_host = '';
    useLocalState.setState({
      host: window.location.origin,
      hostKey: 'C06-identity-fixture',
      hostList: {}
    });
  });
  await select(page, 'A');
  await editor(page).fill('C06 identity draft A');
  await attachReady(page);
  await select(page, 'B');
  await editor(page).fill('C06 identity draft B');
  await select(page, 'A');
  await expect(editor(page)).toHaveValue('C06 identity draft A');
  await expect(attachment(page)).toBeVisible();
  const before = await page.evaluate(
    async (ids) => {
      const userUrl = '/src/states/UserState.tsx';
      const localUrl = '/src/states/LocalState.tsx';
      const stateUrl = '/src/states/AIChatState.tsx';
      const draftUrl = '/src/states/ChatDraftState.ts';
      const user = await import(userUrl);
      const local = await import(localUrl);
      const state = await import(stateUrl);
      const draft = await import(draftUrl);
      const originalUser = user.useUserState.getState().getUser();
      if (!originalUser?.pk) throw new Error('Nonempty fixture actor required');
      const scope = {
        host: local.useLocalState.getState().getHost(),
        userId: originalUser.pk,
        sessionGeneration: state.useAIChatState.getState().sessionGeneration
      };
      const identities = ids.map((id: string) => ({ ...scope, threadId: id }));
      const bindings = identities.map((id) => draft.bindChatDrafts(id));
      (window as any).__c06Identity = {
        user,
        local,
        state,
        draft,
        originalUser,
        scope,
        identities,
        bindings,
        logoutDone: false,
        navigation: [] as string[]
      };
      return {
        generation: scope.sessionGeneration,
        snapshots: bindings.map((binding) => ({
          live: binding.isCurrent(),
          text: binding.read().text,
          files: binding
            .read()
            .files.map((file: { file_id: string }) => file.file_id)
        }))
      };
    },
    [threadId, secondId]
  );
  expect(before.snapshots).toEqual([
    { live: true, text: 'C06 identity draft A', files: [fileId] },
    { live: true, text: 'C06 identity draft B', files: [] }
  ]);
  return { ...fixture, before };
}

async function identitySnapshot(page: Page, lateWrite = false) {
  return page.evaluate((write) => {
    const probe = (window as any).__c06Identity;
    const view = (binding: any) => ({
      live: binding.isCurrent(),
      text: binding.read().text,
      files: binding
        .read()
        .files.map((file: { file_id: string }) => file.file_id)
    });
    const late = write
      ? probe.bindings.map((binding: any) =>
          binding.writeText('C06 stale callback must be denied')
        )
      : [];
    return {
      generation: probe.state.useAIChatState.getState().sessionGeneration,
      snapshots: probe.bindings.map(view),
      freshOldKeys: probe.identities.map((id: any) =>
        view(probe.draft.bindChatDrafts(id))
      ),
      late
    };
  }, lateWrite);
}

for (const kind of identityKinds) {
  test(`C06 identity actual caller: ${kind}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('http://c06-host.invalid/**', async (route) => {
      await route.fulfill({ json: {} });
    });
    const { before, chatPosts } = await prepareIdentity(page);
    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const authDeletes: string[] = [];
    await page.route('**/api/auth/v1/auth/session*', async (route) => {
      if (route.request().method() !== 'DELETE') return route.fallback();
      authDeletes.push(new URL(route.request().url()).pathname);
      await held;
      await route.fulfill({ json: { meta: { is_authenticated: false } } });
    });
    try {
      await page.evaluate(async (transition) => {
        const probe = (window as any).__c06Identity;
        if (transition === 'logout') {
          const authUrl = '/src/functions/auth.tsx';
          const { doLogout } = await import(authUrl);
          void doLogout((target: string) => {
            probe.navigation.push(target);
          }).then(() => {
            probe.logoutDone = true;
          });
        } else if (transition === 'account') {
          probe.user.useUserState
            .getState()
            .setUser({ ...probe.originalUser, pk: 12 });
        } else if (transition === 'host') {
          probe.local.useLocalState
            .getState()
            .setHost('http://c06-host.invalid', 'C06-other');
          if (
            probe.local.useLocalState.getState().getHost() === probe.scope.host
          ) {
            throw new Error('Effective host must actually change');
          }
        } else if (transition === 'profile') {
          probe.user.useUserState.getState().setUser({
            ...probe.originalUser,
            first_name: 'C06 display-only update'
          });
        } else {
          probe.local.useLocalState
            .getState()
            .setHost(probe.scope.host, 'C06-same-effective-host');
        }
      }, kind);
      if (kind === 'logout')
        await expect.poll(() => authDeletes.length).toBe(1);
      const clears = kind === 'logout' || kind === 'account' || kind === 'host';
      const after = await identitySnapshot(page, clears);
      if (clears) {
        expect(after.generation).toBeGreaterThan(before.generation);
        expect(after.snapshots).toEqual([
          { live: false, text: '', files: [] },
          { live: false, text: '', files: [] }
        ]);
        expect(
          after.freshOldKeys.map(
            ({ text, files }: { text: string; files: string[] }) => ({
              text,
              files
            })
          )
        ).toEqual([
          { text: '', files: [] },
          { text: '', files: [] }
        ]);
        expect(after.late).toEqual([-1, -1]);
      } else {
        expect(after.generation).toBe(before.generation);
        expect(after.snapshots).toEqual(before.snapshots);
        await expect(editor(page)).toHaveValue('C06 identity draft A');
        await expect(attachment(page)).toBeVisible();
      }
      release();
      if (kind === 'logout') {
        await expect
          .poll(() =>
            page.evaluate(() => (window as any).__c06Identity.logoutDone)
          )
          .toBe(true);
        expect(
          await page.evaluate(() => (window as any).__c06Identity.navigation)
        ).toEqual(['/login']);
        expect((await identitySnapshot(page)).snapshots).toEqual(
          after.snapshots
        );
      }
      expect(chatPosts).toEqual([]);
      expect(errors).toEqual([]);
      console.log(
        `C06_IDENTITY_OBSERVATIONS ${JSON.stringify({
          kind,
          before,
          after,
          authDeletes,
          errors,
          chatPosts,
          scope:
            'Actual mounted caller/function invocation with wholly mocked auth/API; no real login/logout or physical-device claim'
        })}`
      );
    } finally {
      release();
    }
  });
}
