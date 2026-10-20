/**
 * VOICE-H06 mounted-hook acceptance (mocked network, real useAIChat).
 *
 * Every case drives the REAL summary → hydration path inside useAIChat:
 * the thread detail delivers an id-less ActiveScopeSummary and the full
 * authorized scope GET must hydrate canonical machine ids — guarded against
 * newer scope writes, thread/selection/account switches and authoritative
 * denials. Both summary consumers are exercised: loadThreadFromServer
 * (switchThread) and syncThreads (thread-list sync → active detail).
 * Wire shapes are authentic: AnalysisScopeMode 'explicit_assets' (the
 * generated union has no 'machines' member), full ThreadScopePayload.
 * Mocked browser rendering; a live backend E2E stays parent-side.
 */
import { expect, test } from '@playwright/test';

const state = (page: import('@playwright/test').Page) =>
  page.evaluate(() => {
    const el = document.getElementById('state');
    return JSON.parse(el?.textContent ?? '{}');
  });

const net = (page: import('@playwright/test').Page) =>
  page.evaluate(() => (window as any).__scopeNet);

const scopePayloadJson = (
  threadId: string,
  version: number,
  displayLabel: string,
  machineIds: number[],
  editable = true
) => ({
  thread_id: threadId,
  version,
  hash: `fixture-${threadId}-v${version}`,
  display_label: displayLabel,
  editable,
  scope: {
    schema_version: 1,
    mode: 'explicit_assets',
    machine_ids: machineIds,
    date_window: { from: null, to: null },
    source_classes: [],
    display_label: displayLabel
  }
});

test.beforeEach(async ({ page }) => {
  await page.goto('/playwright/voice-deferred-focus.html');
  await page.waitForFunction(() => Boolean((window as any).__chat));
});

test('summary-derived scope hydrates canonical explicit machine ids (loadThreadFromServer)', async ({
  page
}) => {
  await page.evaluate(
    (body) => {
      (window as any).__scopeNet.scopeMode = 'payload';
      (window as any).__scopeNet.scopeBodies['t-1'] = body;
    },
    scopePayloadJson('t-1', 4, 'Fixture 12', [12, 15])
  );
  await page.evaluate(() => (window as any).__switchThread('t-1'));
  await expect
    .poll(async () => (await state(page)).activeScope?.machineIds)
    .toEqual([12, 15]);
  const s = await state(page);
  expect(s.activeScope.version).toBe(4);
  expect(s.activeScope.displayLabel).toBe('Fixture 12');
  expect(s.activeScope.editable).toBe(true);
});

test('failed/revoked/null scope response keeps summary display without ids', async ({
  page
}) => {
  await page.evaluate(() => {
    (window as any).__scopeNet.scopeMode = 'denied';
  });
  await page.evaluate(() => (window as any).__switchThread('t-2'));
  await expect
    .poll(async () => (await state(page)).activeScope?.displayLabel)
    .toBe('Fixture 12');
  const s = await state(page);
  expect(s.activeScope.machineIds).toBeUndefined();
  expect(s.activeScope.version).toBe(4);
});

test('a stale scope read can never overwrite a newer scope write ack', async ({
  page
}) => {
  await page.evaluate(() => {
    const net = (window as any).__scopeNet;
    net.scopeMode = 'defer';
    net.summaries['t-3'] = {
      mode: 'explicit_assets',
      version: 4,
      display_label: 'Fixture 12'
    };
  });
  await page.evaluate(() => (window as any).__switchThread('t-3'));
  await expect
    .poll(() => net(page).then((n) => n.pending.length))
    .toBeGreaterThan(0);
  // A newer scope PUT lands and is acknowledged while the GET is in flight.
  await page.evaluate(async () => {
    const chat = (window as any).__chat;
    await chat.setThreadScope({ mode: 'explicit_assets', machine_ids: [13] });
    // Now the OLD read resolves late with its own pre-write state.
    const net = (window as any).__scopeNet;
    for (const d of net.pending.splice(0)) {
      d.resolve(
        {
          thread_id: 't-3',
          version: 4,
          hash: 'fixture-t-3-v4',
          display_label: 'Fixture 12',
          editable: true,
          scope: {
            schema_version: 1,
            mode: 'explicit_assets',
            machine_ids: [12, 15],
            date_window: { from: null, to: null },
            source_classes: [],
            display_label: 'Fixture 12'
          }
        },
        200
      );
    }
  });
  await expect
    .poll(async () => (await state(page)).activeScope?.machineIds)
    .toEqual([13]);
  const s = await state(page);
  expect(s.activeScope.version).toBe(5);
  expect(s.activeScope.displayLabel).toBe('Fixture 13');
});

test('rapid thread switch: each read resolves with ITS thread body and the stale response cannot overwrite fresh data', async ({
  page
}) => {
  await page.evaluate(
    (fresh) => {
      const net = (window as any).__scopeNet;
      net.scopeMode = 'defer';
      net.scopeBodies['t-5'] = fresh;
    },
    scopePayloadJson('t-5', 6, 'Fixture 15', [15])
  );
  await page.evaluate(() => (window as any).__switchThread('t-4'));
  await expect
    .poll(() => net(page).then((n) => n.pending.length))
    .toBeGreaterThan(0);
  await page.evaluate(() => (window as any).__switchThread('t-5'));
  await expect.poll(async () => (await state(page)).activeThreadId).toBe('t-5');
  await expect
    .poll(() => net(page).then((n) => n.pending.length))
    .toBeGreaterThan(1);
  // Route each pending GET to its REAL captured thread: the t-4 read gets the
  // stale t-4 body (even released first), the t-5 read gets fresh t-5 data.
  await page.evaluate(
    (stale) => {
      const net = (window as any).__scopeNet;
      const pending = net.pending.splice(0);
      const forThread = (d: { url: string }) =>
        decodeURIComponent(d.url.match(/\/threads\/([^/]+)\/scope$/)![1]);
      // Stale OLD thread response first…
      for (const d of pending.filter(
        (d: { url: string }) => forThread(d) === 't-4'
      )) {
        d.resolve(stale, 200);
      }
      // …then the fresh new thread's own response.
      for (const d of pending.filter(
        (d: { url: string }) => forThread(d) === 't-5'
      )) {
        d.resolve(
          {
            thread_id: 't-5',
            version: 6,
            hash: 'fixture-t-5-v6',
            display_label: 'Fixture 15',
            editable: true,
            scope: {
              schema_version: 1,
              mode: 'explicit_assets',
              machine_ids: [15],
              date_window: { from: null, to: null },
              source_classes: [],
              display_label: 'Fixture 15'
            }
          },
          200
        );
      }
    },
    scopePayloadJson('t-4', 9, 'Fixture 99', [99])
  );
  await expect
    .poll(async () => (await state(page)).activeScope?.machineIds)
    .toEqual([15]);
  await page.waitForTimeout(250);
  const s = await state(page);
  expect(s.activeThreadId).toBe('t-5');
  // The stale t-4 response (v9 'Fixture 99' [99]) must never overwrite.
  expect(s.activeScope.displayLabel).toBe('Fixture 15');
  expect(s.activeScope.version).toBe(6);
  expect(s.activeScope.machineIds).toEqual([15]);
});

test('account switch: the old read is dropped AND a fresh hydration applies for the new account (syncThreads consumer)', async ({
  page
}) => {
  await page.evaluate(() => {
    (window as any).__scopeNet.scopeMode = 'defer';
    (window as any).__switchThread('t-6');
  });
  await expect
    .poll(() => net(page).then((n) => n.pending.length))
    .toBeGreaterThan(0);
  await page.evaluate(() => {
    const net = (window as any).__scopeNet;
    // Real user-store update → the hook rerenders and crosses its account
    // cache-key boundary (chatIndexKey(backendHost, user.userId())).
    net.accountFlip();
  });
  // Prove the TRUE rerendered user/account switch happened BEFORE judging the
  // old read: the hook's account boundary is its render-derived cache key.
  await expect.poll(async () => (await state(page)).accountUserId).toBe(22);
  await page.evaluate(() => {
    const net = (window as any).__scopeNet;
    // The old account's in-flight read resolves late with old-account data.
    // Account rerender also starts a NEW authorized sync. Do not misroute its
    // reads to the old account's response: retain them for the fresh control.
    const oldReads = net.pending.filter(
      (d: { accountUserId: number }) => d.accountUserId === 11
    );
    if (!oldReads.length) throw new Error('Missing old-account scope read');
    net.pending = net.pending.filter(
      (d: { accountUserId: number }) => d.accountUserId !== 11
    );
    for (const d of oldReads) {
      d.resolve(
        {
          thread_id: 't-6',
          version: 9,
          hash: 'fixture-t-6-v9',
          display_label: 'Fixture 99',
          editable: true,
          scope: {
            schema_version: 1,
            mode: 'explicit_assets',
            machine_ids: [99],
            date_window: { from: null, to: null },
            source_classes: [],
            display_label: 'Fixture 99'
          }
        },
        200
      );
    }
  });
  await page.waitForTimeout(250);
  const rejected = await state(page);
  expect(rejected.activeScope?.machineIds ?? []).not.toContain(99);
  // Fresh hydration for the NEW account must APPLY (not be dropped as stale):
  // drive the syncThreads summary consumer (list sync → active detail).
  await page.evaluate(
    (fresh) => {
      const net = (window as any).__scopeNet;
      net.scopeMode = 'payload';
      net.scopeBodies['t-6'] = fresh;
      for (const d of net.pending.splice(0)) {
        if (d.accountUserId !== 22)
          throw new Error('Unexpected account on fresh read');
        d.resolve(fresh, 200);
      }
      net.listBody = {
        threads: [
          {
            thread_id: 't-6',
            title: 'Thread t-6',
            summary: '',
            created_at: '2026-10-06T00:00:00Z',
            last_activity: '2026-10-06T00:00:00Z'
          }
        ],
        sync_token: null,
        has_more: false,
        capabilities: { thread_scope: true, thread_focus: true }
      };
    },
    scopePayloadJson('t-6', 5, 'Fixture 22', [22])
  );
  await page.evaluate(() => (window as any).__syncThreads());
  await expect
    .poll(async () => (await state(page)).activeScope?.machineIds)
    .toEqual([22]);
  const s = await state(page);
  expect(s.activeScope.version).toBe(5);
  expect(s.activeScope.displayLabel).toBe('Fixture 22');
});

test('shared read-only threads show scope but never edit', async ({ page }) => {
  await page.evaluate(
    (body) => {
      const net = (window as any).__scopeNet;
      net.scopeMode = 'payload';
      net.shared['t-7'] = true;
      net.scopeBodies['t-7'] = body;
      net.summaries['t-7'] = {
        mode: 'explicit_assets',
        version: 4,
        display_label: 'Fixture 12'
      };
    },
    scopePayloadJson('t-7', 4, 'Fixture 12', [12, 15], false)
  );
  await page.evaluate(() => (window as any).__switchThread('t-7'));
  await expect
    .poll(async () => (await state(page)).activeScope?.machineIds)
    .toEqual([12, 15]);
  const s = await state(page);
  expect(s.activeThreadShared).toBe(true);
  expect(s.activeScope.editable).toBe(false);
});
